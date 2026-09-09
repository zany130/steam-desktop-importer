# Native Steam Test Environment

Required by IMPLEMENTATION.md §31, Phase 0: "document one current native
Steam test environment."

This records the machine used for Phase 0 format characterization and the
machine that will be used for the Phase 10 native Steam release gate.

## Environment A — primary native Steam host

| Property | Value |
| --- | --- |
| Distribution | Bazzite 44.20260907.0 (Kinoite base) |
| Kernel | 7.2.3-ogc3.1.fc44.x86_64 |
| Desktop | KDE Plasma (`XDG_CURRENT_DESKTOP=KDE`) |
| Session | Wayland and X11 both available |
| Steam | native RPM `steam-1.0.0.87-1.fc44.x86_64` |
| Flatpak Steam | **not installed** |
| Steam root | `~/.local/share/Steam` |
| Steam root aliases | `~/.steam/steam`, `~/.steam/root` (both symlinks to the above) |
| Steam accounts | 1 |
| Existing non-Steam shortcuts | 783 |
| Existing grid artwork files | 1443 |
| `XDG_DATA_HOME` | unset (default `~/.local/share` applies) |
| `XDG_DATA_DIRS` | set explicitly, contains duplicates — see PHASE0_FORMAT_CHARACTERIZATION.md §5 |
| Python | 3.14.7 system; project venv pinned to 3.12 |

### Why this host is a good release-gate target

- It has a large, pre-existing, **third-party-populated** `shortcuts.vdf`.
  IMPLEMENTATION.md §34 requires proving that "unrelated existing shortcuts
  survive repeated imports". 783 entries written by at least two different
  writers, with two different key sets, is a demanding test of that.
- It has all four source kinds available: native RPM apps, Flatpaks, Snaps
  (if snapd is enabled), and integrated AppImages.
- `XDG_DATA_DIRS` is messy in a realistic way (duplicates, a transient
  AppImage mount), which exercises §7.1 ordering and de-duplication.

### Why this host cannot cover everything

- **One Steam account only.** MUST-TEST TEST-002 (account-selection hints)
  and the multi-account confirmation dialog of §13 cannot be validated here.
- **No Flatpak Steam.** §11, Phase 11, and MUST-TEST TEST-001 cannot be
  validated here.

A second environment is required before either of those can move out of
"experimental / untested".

## Environment B — required, not yet available

Needed to close TEST-001 and TEST-002:

| Requirement | Purpose |
| --- | --- |
| Flathub `com.valvesoftware.Steam` with **stock** permissions | TEST-001 step 1: confirm `org.freedesktop.Flatpak` is absent |
| The same host, after a **manual** `flatpak override` | TEST-001 steps 4–7 |
| Two real Steam accounts | TEST-002 |

The importer must never perform the `flatpak override` itself
(IMPLEMENTATION.md §11, §35 rule 23, and TEST-001's closing line).

## Safety rules for anyone using a live environment

1. Nothing in this repository may open a live `shortcuts.vdf` for writing
   until Phase 7 is complete and its failure-injection tests pass
   (IMPLEMENTATION.md §31 Phase 7: "Do not enable live writes until this
   phase passes").
2. Phase 0 capture tooling (`scripts/characterize_shortcuts.py`) is
   read-only by construction.
3. Before any future live write test: fully exit Steam, and independently
   back up `userdata/<id>/config/shortcuts.vdf` outside the repository.
4. Captured personal data must not be committed. `tests/fixtures/captured/`
   is gitignored for ad-hoc local captures.

## Current status of live writes

**Disabled.** No code path in this repository writes to a Steam directory.
Phases 0–1 are parser/discovery only.
