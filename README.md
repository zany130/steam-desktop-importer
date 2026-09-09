# Steam Desktop Importer

A native Linux desktop application that discovers installed applications from
FreeDesktop `.desktop` entries and imports selected ones into Steam as
non-Steam shortcuts, with optional SteamGridDB artwork.

Built to the specification in `IMPLEMENTATION.md`.

## Status

**Phases 0, 1, 2 and 4 are implemented. Nothing else is.**

There is no GUI yet, and no code path writes to a Steam directory. A test
(`test_package_performs_no_filesystem_writes`) enforces that, in line with the
Phase 7 rule "do not enable live writes until this phase passes".

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Fixtures and format characterization | done |
| 1 | XDG discovery and Desktop Entry parsing | done |
| 2 | Launch adapters | done |
| 3 | GUI | not started |
| 4 | Steam install/account discovery | done |
| 5 | Persistent state and AppID allocation | not started |
| 6 | VDF read/update | fixtures only |
| 7 | Safe VDF commit | not started |
| 8–11 | SteamGridDB, artwork UI, release gates | not started |

See `CHECKLIST.md` for per-requirement status, deviations, and the remaining
open issues.

## What works today

```bash
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
steam-desktop-importer debug launch          # summary across all entries

# Show Steam installations and accounts. Read-only.
steam-desktop-importer debug steam
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

## Development

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Phases 0–1 only need `vdf`, `pyxdg` and `pytest`. `PySide6` is declared for
later phases but is not imported by anything yet.

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
  acknowledged, so persistent state is never keyed to an ambiguous ID.
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
- Steam collections are not implemented.
- Flatpak Steam sandbox permissions are never modified.
