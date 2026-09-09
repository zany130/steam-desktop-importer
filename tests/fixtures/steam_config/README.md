# Steam configuration fixtures

Synthetic. Modelled on the real capture in
`docs/PHASE0_FORMAT_CHARACTERIZATION.md` §3. No personal data.

SteamID64 values are derived as `76561197960265728 + account_id32`, with
invented account IDs.

These fixtures exist for Phase 4 (installation/account discovery). Phase 1
does not consume them; they are built now because IMPLEMENTATION.md §31
Phase 0 requires Steam account fixtures before GUI work begins.

## `native_single_account/`

The unambiguous case. Exactly one account, so §13's policy is auto-select
with no dialog.

```text
root/
  config/loginusers.vdf
  steamapps/.gitkeep
  userdata/11111111/config/grid/.gitkeep
registry.vdf
```

`registry.vdf` is stored beside `root/`, not inside it, mirroring the real
layout where it lives at `~/.steam/registry.vdf` while `loginusers.vdf` lives
at `<steam_root>/config/loginusers.vdf`.

## `native_multi_account/`

Deliberately adversarial: **the hints disagree.**

| Account | `account_id32` | `AutoLogin` | `Timestamp` | `registry.vdf` `AutoLoginUser` |
| --- | --- | --- | --- | --- |
| `alpha_user` | 11111111 | `0` | older | **points here** |
| `beta_user` | 22222222 | `1` | newer | — |
| (orphan) | 33333333 | absent from `loginusers.vdf` | — | — |

Three separate hints point three different ways, and a third `userdata/`
directory has no `loginusers.vdf` entry at all.

Required behaviour (§13):

- enumerate `userdata/` **first**, so the orphan account 33333333 is still
  offered;
- use the hints only to rank/preselect;
- **show the selection dialog and require confirmation** — never resolve this
  silently;
- specifically, never pick `beta_user` just because its timestamp is newest.

## `native_no_hints/`

`userdata/` directories exist but there is no `loginusers.vdf` and no
`registry.vdf`. Enumeration must still work and produce accounts with
`account_name = None` and `persona_name = None`. This is the fixture for
"do not require any single field to exist forever".

## `flatpak_steam/`

Mirrors `~/.var/app/com.valvesoftware.Steam/data/Steam`. Present so that
installation discovery can be tested for the native/Flatpak distinction
without a Flatpak Steam installed.

Flatpak Steam remains **experimental** (§11). Having a fixture does not imply
any of its runtime behaviour is validated.

## `not_steam/`

A directory that exists but has no Steam structure. §12 requires validating
by expected structure rather than existence alone, so discovery must reject
this.
