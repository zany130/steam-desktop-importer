"""Tests pinning CURRENT behaviour for unresolved specification questions.

Each test here corresponds to an entry in CHECKLIST.md's "Open issues"
section. They exist so that the behaviour is visible and any change to it is
deliberate. They are **not** an endorsement of the current behaviour.
"""

from __future__ import annotations

from pathlib import Path

from steam_desktop_importer.desktop.discovery import (
    ApplicationRoot,
    CollisionAcknowledgement,
    collision_fingerprint,
    discover_applications,
)
from steam_desktop_importer.desktop.parser import build_application, parse_desktop_entry

from ..conftest import DESKTOP_ENTRIES


def test_resolved_open_1_nonstandard_exec_is_marked_on_the_application(exec_grammar_dir):
    """OPEN-1 is resolved by DEV-8: strict first, per-entry compatibility retry.

    Kept here so the resolution stays visible next to the issues it came from.
    The tokenizer behaviour itself is covered in ``test_exec_parser.py``.
    """
    parsed = parse_desktop_entry(exec_grammar_dir / "exec-single-quote-shell-style.desktop")
    app = build_application(parsed, desktop_id="exec-single-quote-shell-style.desktop")

    assert app.nonstandard_exec is True
    assert app.exec_parse_mode == "compat"
    assert "EMU Stuff" in app.exec_argv

    # Ordinary entries are untouched: compatibility parsing is per entry.
    ordinary = build_application(
        parse_desktop_entry(exec_grammar_dir / "exec-quoting.desktop"),
        desktop_id="exec-quoting.desktop",
    )
    assert ordinary.nonstandard_exec is False
    assert ordinary.exec_parse_mode == "strict"


def test_resolved_open_2_collisions_resolve_to_one_entry_but_block_import():
    """OPEN-2/2a: the FreeDesktop ID scheme is not injective.

    ``vendor/app.desktop`` and ``vendor-app.desktop`` in the same root both
    derive to ``vendor-app.desktop``. Observed for real on the capture host.

    Identity and importability are separated. Discovery still resolves exactly
    one entry for the ID, keeping §6's state key untouched, but withholds
    import consent so a state row can never be keyed to an ambiguous ID.
    """
    root = DESKTOP_ENTRIES / "id_collision" / "applications"
    flat = root / "vendor-app.desktop"
    nested = root / "vendor" / "app.desktop"

    result = discover_applications(roots=[ApplicationRoot(root, "test")])

    # One effective application per desktop ID.
    assert list(result.applications) == ["vendor-app.desktop"]
    app = result.applications["vendor-app.desktop"]
    assert app.name == "Flat File With A Dash"
    assert result.shadowed == [("vendor-app.desktop", nested)]

    # ... but it is not importable until the ambiguity is acknowledged.
    assert app.supported_for_import is False
    assert app.unsupported_code == "desktop_id_collision"
    assert str(nested) in (app.unsupported_reason or "")
    assert result.importable == []

    # Every colliding path is retained for debugging, winner included.
    assert app.has_collision is True
    assert app.collision_paths == [flat, nested]

    # The diagnostic names the losing file, which `shadowed` alone does not
    # distinguish from ordinary cross-root precedence.
    assert len(result.collisions) == 1
    collision = result.collisions[0]
    assert collision.desktop_id == "vendor-app.desktop"
    assert collision.root == root
    assert collision.paths == (flat, nested)
    assert collision.winner == flat


def test_resolved_open_2_acknowledging_a_collision_lifts_the_import_block():
    """The block is consent, not a permanent verdict; the diagnostic remains."""
    root = DESKTOP_ENTRIES / "id_collision" / "applications"
    flat = root / "vendor-app.desktop"
    nested = root / "vendor" / "app.desktop"
    result = discover_applications(
        roots=[ApplicationRoot(root, "test")],
        acknowledged_collisions=[
            CollisionAcknowledgement("vendor-app.desktop", flat, (flat, nested))
        ],
    )

    app = result.applications["vendor-app.desktop"]
    assert app.supported_for_import is True
    assert app.unsupported_code is None
    assert result.importable == [app]

    # Acknowledging suppresses the block, never the evidence.
    assert app.collision_paths == [root / "vendor-app.desktop", root / "vendor" / "app.desktop"]
    assert len(result.collisions) == 1


def test_resolved_open_2_a_collision_in_a_lower_root_is_not_reported(tmp_path):
    """Precedence already settles cross-root duplicates, so there is no tie.

    Only files that collide at the *same* precedence level need a tie-break.
    A lower root's collision cannot change the outcome, so it is shadowed
    normally and must not withhold import consent from the winning entry.
    """
    entry = "[Desktop Entry]\nType=Application\nName=X\nExec=/bin/true\n"

    high = tmp_path / "high" / "applications"
    high.mkdir(parents=True)
    (high / "vendor-app.desktop").write_text(entry)

    low = tmp_path / "low" / "applications"
    (low / "vendor").mkdir(parents=True)
    (low / "vendor-app.desktop").write_text(entry)
    (low / "vendor" / "app.desktop").write_text(entry)

    result = discover_applications(
        roots=[ApplicationRoot(high, "test"), ApplicationRoot(low, "test")]
    )

    app = result.applications["vendor-app.desktop"]
    assert app.source_root == high
    assert app.supported_for_import is True
    assert app.collision_paths == []
    assert result.collisions == []
    assert len(result.shadowed) == 2


def test_resolved_open_2_an_unparsable_winner_yields_to_the_next_candidate(tmp_path):
    """Unparsable files never claim an ID, and that rule survives collisions."""
    root = tmp_path / "applications"
    (root / "vendor").mkdir(parents=True)
    (root / "vendor-app.desktop").write_text("this is not a desktop entry\n")
    (root / "vendor" / "app.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Nested Winner\nExec=/bin/true\n"
    )

    result = discover_applications(roots=[ApplicationRoot(root, "test")])

    app = result.applications["vendor-app.desktop"]
    assert app.name == "Nested Winner"
    assert app.desktop_path == root / "vendor" / "app.desktop"
    assert [path for path, _ in result.errors] == [root / "vendor-app.desktop"]

    # The lexically first path still counts as colliding even though it lost
    # on parseability rather than on ordering.
    assert result.collisions[0].winner == root / "vendor" / "app.desktop"
    assert app.supported_for_import is False


def test_resolved_open_2_an_already_unsupported_winner_keeps_its_own_reason(tmp_path):
    """A collision must not mask a more useful parse-level explanation."""
    root = tmp_path / "applications"
    (root / "vendor").mkdir(parents=True)
    (root / "vendor-app.desktop").write_text("[Desktop Entry]\nType=Application\nName=No Exec\n")
    (root / "vendor" / "app.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=X\nExec=/bin/true\n"
    )

    result = discover_applications(roots=[ApplicationRoot(root, "test")])

    app = result.applications["vendor-app.desktop"]
    assert app.supported_for_import is False
    assert app.unsupported_code == "no_exec"
    assert app.has_collision is True


def _write_app(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec=/bin/true\n",
        encoding="utf-8",
    )


def test_acknowledgement_for_the_losing_file_does_not_lift_the_winner():
    """Consent is bound to the physical winner, not just the desktop ID."""
    root = DESKTOP_ENTRIES / "id_collision" / "applications"
    flat = root / "vendor-app.desktop"
    nested = root / "vendor" / "app.desktop"
    result = discover_applications(
        roots=[ApplicationRoot(root, "test")],
        acknowledged_collisions=[
            CollisionAcknowledgement("vendor-app.desktop", nested, (flat, nested))
        ],
    )
    app = result.applications["vendor-app.desktop"]
    assert app.desktop_path == flat
    assert app.supported_for_import is False


def test_acknowledgement_does_not_follow_a_new_physical_winner(tmp_path):
    """If the lexical/parse winner changes, the stored consent is stale."""
    root = tmp_path / "applications"
    first = root / "vendor-app.desktop"
    second = root / "vendor" / "app.desktop"
    _write_app(first, "First")
    _write_app(second, "Second")
    ack = CollisionAcknowledgement("vendor-app.desktop", first, (first, second))

    lifted = discover_applications(
        roots=[ApplicationRoot(root, "test")],
        acknowledged_collisions=[ack],
    )
    assert lifted.applications["vendor-app.desktop"].supported_for_import is True

    first.write_text("this is not a desktop entry\n", encoding="utf-8")
    stale = discover_applications(
        roots=[ApplicationRoot(root, "test")],
        acknowledged_collisions=[ack],
    )
    app = stale.applications["vendor-app.desktop"]
    assert app.desktop_path == second
    assert app.supported_for_import is False
    assert app.unsupported_code == "desktop_id_collision"


def test_acknowledgement_is_invalid_when_the_collision_set_grows(tmp_path):
    """A new slash/dash encoding of the same ID is a different collision."""
    root = tmp_path / "applications"
    first = root / "vendor-app-extra.desktop"
    second = root / "vendor" / "app-extra.desktop"
    third = root / "vendor-app" / "extra.desktop"
    _write_app(first, "First")
    _write_app(second, "Second")
    ack = CollisionAcknowledgement(
        "vendor-app-extra.desktop", first, (first, second)
    )

    _write_app(third, "Third")
    result = discover_applications(
        roots=[ApplicationRoot(root, "test")],
        acknowledged_collisions=[ack],
    )
    app = result.applications["vendor-app-extra.desktop"]
    assert app.supported_for_import is False
    assert set(app.collision_paths) == {first, second, third}


def test_acknowledgement_is_invalid_when_the_collision_set_shrinks(tmp_path):
    """A leftover third file going away is still a material set change.

    The remaining pair is a *different* collision than the one that was
    acknowledged, so consent is required again.
    """
    root = tmp_path / "applications"
    first = root / "vendor-app-extra.desktop"
    second = root / "vendor" / "app-extra.desktop"
    third = root / "vendor-app" / "extra.desktop"
    _write_app(first, "First")
    _write_app(second, "Second")
    _write_app(third, "Third")
    ack = CollisionAcknowledgement(
        "vendor-app-extra.desktop", first, (first, second, third)
    )
    third.unlink()
    result = discover_applications(
        roots=[ApplicationRoot(root, "test")],
        acknowledged_collisions=[ack],
    )
    app = result.applications["vendor-app-extra.desktop"]
    assert app.supported_for_import is False
    assert set(app.collision_paths) == {first, second}


def test_collision_fingerprint_changes_with_winner_or_set(tmp_path):
    first = tmp_path / "a.desktop"
    second = tmp_path / "b.desktop"
    same = collision_fingerprint(first, (first, second))
    assert same == collision_fingerprint(first, (second, first))
    assert same != collision_fingerprint(second, (first, second))
    assert same != collision_fingerprint(first, (first, second, tmp_path / "c.desktop"))


def test_open_3_flatpak_file_forwarding_markers_survive_field_code_removal(sources_dir):
    """OPEN-3: dropping %U leaves the surrounding @@u ... @@ markers behind.

    ``--file-forwarding`` wraps document arguments in ``@@u`` and ``@@``. When
    no document is passed, ``%U`` correctly expands to nothing but the markers
    remain, so the launch vector still contains them. Confirmed against a real
    Flatpak export on the capture host.

    Resolving this belongs to the Phase 2 Flatpak launch adapter, not to the
    Phase 1 parser, which is correct to leave non-field-code tokens alone.
    """
    directory = sources_dir / "flatpak_user_exports" / "share" / "applications"
    parsed = parse_desktop_entry(directory / "org.example.FlatpakApp.desktop")
    app = build_application(parsed, desktop_id="org.example.FlatpakApp.desktop")

    assert app.exec_argv[-2:] == ["@@u", "@@"]
    assert "%U" not in app.exec_argv
    # The app ID is still recovered correctly despite the trailing markers.
    assert app.flatpak_id == "org.example.FlatpakApp"


def test_open_4_quoted_boolean_falls_back_to_the_default(tmp_path: Path):
    """OPEN-4: ``Terminal='False'`` appears in the wild (7 entries observed).

    Only ``true``/``false`` are valid. A quoted value is invalid and falls back
    to the default, which here happens to give the intended result, but only
    by luck. A quoted ``'True'`` would silently become False.
    """
    path = tmp_path / "quoted.desktop"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Q\nExec=/bin/true\nTerminal='True'\n"
    )
    parsed = parse_desktop_entry(path)
    assert parsed.terminal is False
    assert any("not a valid boolean" in warning for warning in parsed.warnings)
