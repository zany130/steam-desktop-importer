"""Tests pinning CURRENT behaviour for unresolved specification questions.

Each test here corresponds to an entry in CHECKLIST.md's "Open issues"
section. They exist so that the behaviour is visible and any change to it is
deliberate. They are **not** an endorsement of the current behaviour.
"""

from __future__ import annotations

from pathlib import Path

from steam_desktop_importer.desktop.discovery import ApplicationRoot, discover_applications
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


def test_open_2_desktop_ids_can_collide_between_nested_and_dashed_paths():
    """OPEN-2: the FreeDesktop ID scheme is not injective.

    ``vendor/app.desktop`` and ``vendor-app.desktop`` in the same root both
    derive to ``vendor-app.desktop``. Observed for real on the capture host.
    §6 keys persistent importer state on the desktop ID, so a collision means
    two different applications would share one state row.
    """
    root = DESKTOP_ENTRIES / "id_collision" / "applications"
    result = discover_applications(roots=[ApplicationRoot(root, "test")])

    assert list(result.applications) == ["vendor-app.desktop"]
    # §7.4's "first match wins" resolves it deterministically, and the loser
    # is recorded rather than silently discarded.
    assert result.applications["vendor-app.desktop"].name == "Flat File With A Dash"
    assert result.shadowed == [("vendor-app.desktop", root / "vendor" / "app.desktop")]


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
