# Steam Desktop Importer

A native Linux desktop application that discovers installed applications from
FreeDesktop `.desktop` entries and imports selected ones into Steam as
non-Steam shortcuts, with optional SteamGridDB artwork.

Built to the specification in `IMPLEMENTATION.md`.

## Status

**Phases 0–12 are implemented** on native Steam. Phase 11 Flatpak Steam host
launching is experimental and fixture-tested (TEST-001 live launch is not
validated here). v1.0.0 ships as source/venv and an AppImage.

The GUI can import selected applications into `shortcuts.vdf` when Steam is
closed. It never writes Steam userdata while Steam is running. With
`SGDB_API_KEY` (or a saved key), it can search SteamGridDB, preview artwork,
and write selected images into the account's `config/grid/` directory after
the VDF commit. Without a key, import is shortcut-only. Checked collections
(or a typed new name) are written afterwards into Steam's cloud-storage JSON;
they are never invented by default. Flatpak Steam imports wrap host commands
with `flatpak-spawn --host` and never grant sandbox permissions.

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Fixtures and format characterization | done |
| 1 | XDG discovery and Desktop Entry parsing | done |
| 2 | Launch adapters | done |
| 3 | GUI | done |
| 4 | Steam install/account discovery | done |
| 5 | Persistent state and AppID allocation | done |
| 6 | VDF read/update | done |
| 7 | Safe VDF commit | done |
| 8 | SteamGridDB client | done |
| 9 | Artwork UI and `grid/` placement | done |
| 10 | Native Steam release gate | done |
| 11 | Flatpak Steam experimental adapter | implemented (fixture-tested; live TEST-001 pending) |
| 12 | Steam collections | implemented (Steam-closed writes; no live-Steam edits) |
| — | Release packaging (AppImage for v1.0.0; Flatpak later) | AppImage recipe; Flatpak of this importer not in 1.0.0 |

See `CHECKLIST.md` for per-requirement status, deviations, and the remaining
open issues.

## What works today

```bash
# Open the GUI. Import writes Steam files only while Steam is closed.
steam-desktop-importer

# Show the ordered applications/ roots that will be scanned.
steam-desktop-importer debug roots

# Discover and resolve every desktop entry.
steam-desktop-importer debug scan --importable-only

# List only entries whose Exec needed the compatibility tokenizer.
steam-desktop-importer debug scan --nonstandard-only

# Explain one desktop file in full.
steam-desktop-importer debug desktop-entry /usr/share/applications/org.kde.kate.desktop

# Show the command a shortcut would run. Never executes it, never writes.
steam-desktop-importer debug launch us.zoom.Zoom.desktop
steam-desktop-importer debug launch --flatpak-steam us.zoom.Zoom.desktop
steam-desktop-importer debug launch          # summary across all entries

# Show Steam installations and accounts. Read-only.
steam-desktop-importer debug steam

# Show §6/§16 identity for one desktop ID. Never writes Steam or state.
steam-desktop-importer debug identity org.kde.kate.desktop

# Show parsed shortcuts.vdf. Read-only; never writes.
steam-desktop-importer debug dump-shortcuts

# Fingerprint shortcuts.vdf + grid/ without names. Read-only; never writes.
steam-desktop-importer debug snapshot-shortcuts > /tmp/sdi-before.json
steam-desktop-importer debug compare-snapshots /tmp/sdi-before.json /tmp/sdi-after.json --ignore-appid 3511661831

# List Steam library collections. Read-only; never writes.
steam-desktop-importer debug collections

# Search SteamGridDB. Needs SGDB_API_KEY or a saved key. Never writes Steam.
steam-desktop-importer debug steamgriddb search Kate
steam-desktop-importer debug steamgriddb grids 2254 --dimensions 600x900
```

On the development host `debug scan` resolves 804 entries with 0 parse errors,
4 masked by `Hidden=true`, 9 shadowed lower-priority copies, and 32 entries
whose `Exec=` uses non-standard single-quote quoting. Two entries are held
back from import, leaving 777 importable: one desktop ID collision and one
`Exec=` whose shell quote-escaping we cannot parse correctly.

All 777 produce a launch vector — 503 native, 237 Flatpak, 37 AppImage — with
no warnings and no leftover Flatpak file-forwarding markers. For example
`us.zoom.Zoom` becomes:

```text
exe         /usr/bin/flatpak
arguments   ['run', '--branch=stable', '--arch=x86_64', '--command=zoom', 'us.zoom.Zoom']
StartDir    (empty)
```

`debug steam` finds one native installation there, correctly collapsing the
three paths that point at it (`~/.local/share/Steam`, `~/.steam/steam`,
`~/.steam/root`) into a single entry, and one account. Both resolve without a
prompt because neither is ambiguous — with two of either, the importer
preselects but refuses to decide.

Phase 5 adds a SQLite store at `$XDG_STATE_HOME/steam-desktop-importer/`
and importer-owned AppID allocation. On this host an empty store classifies
all 804 resolved entries as New. The live `shortcuts.vdf` now has 983 identities (Phase 0 recorded 783;
later 961 from Steam ROM Manager; this importer then added Konsole as the
Phase 10 native probe). None of the pre-existing AppIDs collided with that
first-import candidate, and none paired name+exe with a desktop entry, so
Possible Existing Match stayed 0. `Imported` means managed in importer state.
A successful import writes the VDF first, then the mapping.

Phase 6 can load, update by AppID, create a Steam-schema entry, and
serialize binary KeyValues in memory. A no-op load/dumps of the live
961-entry file is byte-identical. Phase 7 replaces a target file through
the atomic transaction; tests use tmp copies, not this host's live VDF.
Phase 8 talks to SteamGridDB with `SGDB_API_KEY` or a 0600 key file under
`$XDG_CONFIG_HOME/steam-desktop-importer/`. Phase 9 places selected artwork
into a *target* userdata `config/grid/` using the unsigned 32-bit AppID
after the VDF commit. Unit tests use tmp directories; this host's live
Steam grid is not written here.

## Development

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Phases 0–1 only need `vdf`, `pyxdg` and `pytest`. The GUI needs `PySide6`.

Build a host AppImage (does not write Steam):

```bash
./scripts/build_appimage.sh
```

The image lands in `dist/Steam_Desktop_Importer-<version>-<arch>.AppImage`.
It is a normal host binary with host filesystem access. It still refuses to
write Steam userdata while Steam is running.

Regenerate the binary VDF fixtures (they are committed, so this is only needed
if the generator changes):

```bash
python scripts/build_vdf_fixtures.py
```

## Documentation

| File | Contents |
| --- | --- |
| `IMPLEMENTATION.md` | The authoritative specification |
| `CHECKLIST.md` | Requirement-by-requirement status, deviations, open issues |
| `docs/PHASE0_FORMAT_CHARACTERIZATION.md` | What was actually observed on a real Steam install |
| `docs/TEST_ENVIRONMENT.md` | The documented native Steam test environment |
| `tests/fixtures/desktop_entries/README.md` | Fixture index and Phase 1 coverage mapping |
| `tests/fixtures/steam_config/README.md` | Steam account fixture index |

## Safety rules in force

These come from `IMPLEMENTATION.md` §35 and are honoured by the current code:

- `.desktop` files are not treated as INI plus shell commands; `configparser`
  and `shlex` are not used.
- The strict FreeDesktop `Exec=` grammar is always tried first. Compatibility
  parsing is never global: it is retried per entry, only for a recognised
  non-standard quoting pattern that the strict grammar would mis-tokenize, and
  the entry is then marked `nonstandard_exec` / `exec_parse_mode="compat"`.
- When neither grammar can reproduce what a shell would do — currently the
  POSIX `'\''` quote-escaping idiom — the entry is marked unsupported rather
  than launched with an argv that is known to be wrong. A warning alone is not
  enough to keep a broken command out of Steam.
- Applications are keyed by FreeDesktop desktop ID, never by basename.
- The desktop ID scheme is not injective, so two files in one root can derive
  the same ID. Discovery breaks the tie deterministically to keep exactly one
  entry per ID, then withholds import consent until the collision is
  acknowledged for that physical winner and colliding set, so persistent
  state is never keyed to an ambiguous ID. A later scan that resolves a
  different winner or a different colliding set requires confirmation again.
- `Hidden=true` masks lower-priority copies; `NoDisplay=true` does not.
- `env VAR=value` wrappers are preserved verbatim in argv.
- `StartDir` comes from `Path=` or stays empty; it is never inferred from the
  executable's parent directory.
- CRC32 is not claimed to be Steam's AppID algorithm.
- No Steam installation or account is chosen silently. One of either
  auto-selects; several are ranked and preselected, but require confirmation.
- Steam's account files are treated as optional hints, never as a stable API.
  `userdata/` is the source of truth, so an account missing from
  `loginusers.vdf` is still offered and a malformed file degrades to "no
  hints" rather than an error.
- Steam collections are written to cloud-storage JSON while Steam is closed,
  never by stuffing names into `shortcuts.vdf` `tags`. Nothing is added
  unless you check a collection or type a new name.
- `shortcuts.vdf`, `config/grid/`, and collection JSON are never written
  while Steam is running. Artwork hot reload is out of scope.
- Flatpak Steam sandbox permissions are never modified.
