"""SQLite persistent importer state (IMPLEMENTATION.md §6, Phase 5).

Writes only to ``$XDG_STATE_HOME/steam-desktop-importer/state.sqlite3``.
This module must never open a Steam path for writing.

Logical identity is ``(steam_installation_key, steam_account_id32,
desktop_id)``. The persisted unsigned AppID is authoritative for a row that
already exists; ``Name=`` / ``Exec=`` updates change ``last_known_*`` and
``updated_at`` only.

§27 says the VDF commit happens *before* the state commit. This store
therefore does not allocate-and-insert in one step: callers persist a mapping
only after a successful VDF write.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..desktop.discovery import CollisionAcknowledgement, normalize_collision_path

__all__ = [
    "DEFAULT_STEAM_POLL_MS",
    "MAX_STEAM_POLL_MS",
    "MIN_STEAM_POLL_MS",
    "ManagedMapping",
    "StateStore",
    "clamp_steam_poll_ms",
    "default_state_path",
    "xdg_state_home",
]

DEFAULT_STEAM_POLL_MS = 2000
MIN_STEAM_POLL_MS = 1000
MAX_STEAM_POLL_MS = 60_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mappings (
    steam_installation_key TEXT NOT NULL,
    steam_account_id32 INTEGER NOT NULL,
    desktop_id TEXT NOT NULL,
    steam_appid_unsigned INTEGER NOT NULL,
    last_known_name TEXT,
    last_known_exec TEXT,
    desktop_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (steam_installation_key, steam_account_id32, desktop_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS mappings_appid_unique
    ON mappings (steam_installation_key, steam_account_id32, steam_appid_unsigned);

CREATE TABLE IF NOT EXISTS remembered_installation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    steam_installation_key TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remembered_accounts (
    steam_installation_key TEXT PRIMARY KEY,
    steam_account_id32 INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS acknowledged_collisions (
    desktop_id TEXT PRIMARY KEY,
    winner_path TEXT NOT NULL,
    colliding_paths TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    steam_poll_enabled INTEGER NOT NULL DEFAULT 1,
    steam_poll_ms INTEGER NOT NULL DEFAULT 2000,
    updated_at TEXT NOT NULL
);
"""

_ACK_REQUIRED_COLUMNS = frozenset(
    {"desktop_id", "winner_path", "colliding_paths", "fingerprint", "acknowledged_at"}
)


def xdg_state_home(environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """``$XDG_STATE_HOME``, defaulting to ``~/.local/state``.

    A relative value is invalid and falls back to the default, matching the
    XDG data-home rule already used for discovery.
    """
    env = os.environ if environ is None else environ
    base = home if home is not None else Path.home()
    value = env.get("XDG_STATE_HOME")
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    return base / ".local" / "state"


def default_state_path(environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    return xdg_state_home(environ, home) / "steam-desktop-importer" / "state.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clamp_steam_poll_ms(value: int) -> int:
    return max(MIN_STEAM_POLL_MS, min(MAX_STEAM_POLL_MS, int(value)))


@dataclass(frozen=True)
class ManagedMapping:
    """One §6 row."""

    steam_installation_key: str
    steam_account_id32: int
    desktop_id: str
    steam_appid_unsigned: int
    last_known_name: str | None
    last_known_exec: str | None
    desktop_path: str | None
    created_at: str
    updated_at: str


def _row_to_acknowledgement(row: sqlite3.Row) -> CollisionAcknowledgement:
    stored = json.loads(row["colliding_paths"])
    paths = tuple(Path(item) for item in stored)
    return CollisionAcknowledgement(
        desktop_id=row["desktop_id"],
        winner_path=Path(row["winner_path"]),
        colliding_paths=paths,
    )


def _row_to_mapping(row: sqlite3.Row) -> ManagedMapping:
    return ManagedMapping(
        steam_installation_key=row["steam_installation_key"],
        steam_account_id32=row["steam_account_id32"],
        desktop_id=row["desktop_id"],
        steam_appid_unsigned=row["steam_appid_unsigned"],
        last_known_name=row["last_known_name"],
        last_known_exec=row["last_known_exec"],
        desktop_path=row["desktop_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class StateStore:
    """SQLite-backed importer state."""

    def __init__(self, path: Path | str, *, create: bool = True) -> None:
        """Open a store.

        ``create=True`` (the GUI default) makes the state directory and file.
        ``create=False`` is for inspection: an existing file is opened, a
        missing file becomes an empty in-memory store so debug commands and
        characterization cannot create ``state.sqlite3`` as a side effect.
        """
        self.path = Path(path) if path != ":memory:" else Path(":memory:")
        self._memory = path == ":memory:"
        if self._memory:
            self._connection = sqlite3.connect(":memory:")
        elif create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path)
        elif self.path.is_file():
            self._connection = sqlite3.connect(self.path)
        else:
            self._memory = True
            self.path = Path(":memory:")
            self._connection = sqlite3.connect(":memory:")
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)
        self._discard_unbound_acknowledgements()
        self._connection.commit()

    def _discard_unbound_acknowledgements(self) -> None:
        """Drop desktop-ID-only acknowledgement rows from the Phase 5 schema.

        Those rows cannot name a physical winner, so they must not survive
        into a store that treats acknowledgement as consent to import.
        """
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(acknowledged_collisions)")
        }
        if not columns or _ACK_REQUIRED_COLUMNS <= columns:
            return
        self._connection.execute("DROP TABLE acknowledged_collisions")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- mappings -------------------------------------------------------

    def get_mapping(
        self,
        steam_installation_key: str,
        steam_account_id32: int,
        desktop_id: str,
    ) -> ManagedMapping | None:
        row = self._connection.execute(
            """
            SELECT * FROM mappings
            WHERE steam_installation_key = ?
              AND steam_account_id32 = ?
              AND desktop_id = ?
            """,
            (steam_installation_key, steam_account_id32, desktop_id),
        ).fetchone()
        return _row_to_mapping(row) if row else None

    def list_mappings(
        self,
        steam_installation_key: str,
        steam_account_id32: int,
    ) -> list[ManagedMapping]:
        rows = self._connection.execute(
            """
            SELECT * FROM mappings
            WHERE steam_installation_key = ? AND steam_account_id32 = ?
            ORDER BY desktop_id
            """,
            (steam_installation_key, steam_account_id32),
        ).fetchall()
        return [_row_to_mapping(row) for row in rows]

    def occupied_appids(
        self,
        steam_installation_key: str,
        steam_account_id32: int,
    ) -> set[int]:
        rows = self._connection.execute(
            """
            SELECT steam_appid_unsigned FROM mappings
            WHERE steam_installation_key = ? AND steam_account_id32 = ?
            """,
            (steam_installation_key, steam_account_id32),
        ).fetchall()
        return {int(row["steam_appid_unsigned"]) for row in rows}

    def save_mapping(
        self,
        steam_installation_key: str,
        steam_account_id32: int,
        desktop_id: str,
        steam_appid_unsigned: int,
        last_known_name: str | None,
        last_known_exec: str | None,
        desktop_path: str | Path | None,
    ) -> ManagedMapping:
        """Insert or update a mapping.

        If the identity already exists the AppID is **kept**, even if the
        caller passed a different one. That is the §6 / §16 rule: Name and
        Exec may change; the AppID does not.
        """
        existing = self.get_mapping(steam_installation_key, steam_account_id32, desktop_id)
        now = _now()
        path_text = str(desktop_path) if desktop_path is not None else None
        appid = existing.steam_appid_unsigned if existing is not None else steam_appid_unsigned
        created = existing.created_at if existing is not None else now
        self._connection.execute(
            """
            INSERT INTO mappings (
                steam_installation_key, steam_account_id32, desktop_id,
                steam_appid_unsigned, last_known_name, last_known_exec,
                desktop_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (steam_installation_key, steam_account_id32, desktop_id)
            DO UPDATE SET
                last_known_name = excluded.last_known_name,
                last_known_exec = excluded.last_known_exec,
                desktop_path = excluded.desktop_path,
                updated_at = excluded.updated_at
            """,
            (
                steam_installation_key,
                steam_account_id32,
                desktop_id,
                appid,
                last_known_name,
                last_known_exec,
                path_text,
                created,
                now,
            ),
        )
        self._connection.commit()
        mapping = self.get_mapping(steam_installation_key, steam_account_id32, desktop_id)
        assert mapping is not None
        return mapping

    # -- remembered install / account -----------------------------------

    def remember_installation(self, steam_installation_key: str) -> None:
        self._connection.execute(
            """
            INSERT INTO remembered_installation (id, steam_installation_key, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                steam_installation_key = excluded.steam_installation_key,
                updated_at = excluded.updated_at
            """,
            (steam_installation_key, _now()),
        )
        self._connection.commit()

    def remembered_installation(self) -> str | None:
        row = self._connection.execute(
            "SELECT steam_installation_key FROM remembered_installation WHERE id = 1"
        ).fetchone()
        return str(row["steam_installation_key"]) if row else None

    def remember_account(self, steam_installation_key: str, steam_account_id32: int) -> None:
        self._connection.execute(
            """
            INSERT INTO remembered_accounts (
                steam_installation_key, steam_account_id32, updated_at
            ) VALUES (?, ?, ?)
            ON CONFLICT (steam_installation_key) DO UPDATE SET
                steam_account_id32 = excluded.steam_account_id32,
                updated_at = excluded.updated_at
            """,
            (steam_installation_key, steam_account_id32, _now()),
        )
        self._connection.commit()

    def remembered_account(self, steam_installation_key: str) -> int | None:
        row = self._connection.execute(
            """
            SELECT steam_account_id32 FROM remembered_accounts
            WHERE steam_installation_key = ?
            """,
            (steam_installation_key,),
        ).fetchone()
        return int(row["steam_account_id32"]) if row else None

    # -- collision acknowledgements -------------------------------------

    def acknowledge_collision(
        self,
        desktop_id: str,
        winner_path: Path | str,
        colliding_paths: Iterable[Path | str],
    ) -> CollisionAcknowledgement:
        """Remember a collision acknowledgement bound to a physical winner.

        Still global (not per Steam account): the collision is a property of
        the host filesystem. The row is only reused when a later scan has the
        same winner and the same colliding set. Import identity remains
        ``(installation, account, desktop_id)``.
        """
        ack = CollisionAcknowledgement(
            desktop_id=desktop_id,
            winner_path=Path(winner_path),
            colliding_paths=tuple(Path(path) for path in colliding_paths),
        )
        if not ack.colliding_paths:
            raise ValueError("cannot acknowledge a collision without source paths")
        paths_json = json.dumps(
            sorted({normalize_collision_path(path) for path in ack.colliding_paths})
        )
        self._connection.execute(
            """
            INSERT INTO acknowledged_collisions (
                desktop_id, winner_path, colliding_paths, fingerprint, acknowledged_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (desktop_id) DO UPDATE SET
                winner_path = excluded.winner_path,
                colliding_paths = excluded.colliding_paths,
                fingerprint = excluded.fingerprint,
                acknowledged_at = excluded.acknowledged_at
            """,
            (
                ack.desktop_id,
                normalize_collision_path(ack.winner_path),
                paths_json,
                ack.fingerprint,
                _now(),
            ),
        )
        self._connection.commit()
        return ack

    def acknowledged_collisions(self) -> tuple[CollisionAcknowledgement, ...]:
        rows = self._connection.execute(
            """
            SELECT desktop_id, winner_path, colliding_paths
            FROM acknowledged_collisions
            ORDER BY desktop_id
            """
        ).fetchall()
        return tuple(_row_to_acknowledgement(row) for row in rows)

    # -- preferences ----------------------------------------------------

    def steam_poll_enabled(self) -> bool:
        """Whether Steam-running detection is used to gate writes.

        ``False`` is an override for detector false positives. It disables the
        live poll, Import/Relink gating, and the commit-time probe.
        """
        row = self._connection.execute(
            "SELECT steam_poll_enabled FROM preferences WHERE id = 1"
        ).fetchone()
        return True if row is None else bool(row["steam_poll_enabled"])

    def steam_poll_ms(self) -> int:
        row = self._connection.execute(
            "SELECT steam_poll_ms FROM preferences WHERE id = 1"
        ).fetchone()
        if row is None:
            return DEFAULT_STEAM_POLL_MS
        return clamp_steam_poll_ms(int(row["steam_poll_ms"]))

    def set_steam_poll(self, *, enabled: bool, interval_ms: int) -> None:
        self._connection.execute(
            """
            INSERT INTO preferences (id, steam_poll_enabled, steam_poll_ms, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                steam_poll_enabled = excluded.steam_poll_enabled,
                steam_poll_ms = excluded.steam_poll_ms,
                updated_at = excluded.updated_at
            """,
            (1 if enabled else 0, clamp_steam_poll_ms(interval_ms), _now()),
        )
        self._connection.commit()
