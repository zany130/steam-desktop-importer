# Phase 10 — Native Steam release gate

Live procedure from IMPLEMENTATION.md §31 Phase 10, on Environment A
(`docs/TEST_ENVIRONMENT.md`). Personal shortcut names from the pre-existing
library are not recorded here. Fingerprints hash entry payloads and grid
files; they do not store `AppName` / `Exe`.

Unit tests never point at this userdata. The live writes below went only
through `steam/commit.py` and `steam/artwork.py`.

## Tooling added

```text
steam-desktop-importer debug snapshot-shortcuts
steam-desktop-importer debug compare-snapshots before.json after.json --ignore-appid <unsigned>
```

`snapshot-shortcuts` prints JSON to stdout (counts, SHA-256 digests, key
order). `compare-snapshots` exits `1` if an unmanaged shortcut or grid file
changed. Covered by `tests/unit/test_snapshot.py` and
`tests/unit/test_debug_snapshot.py`.

## Mechanical pass — 2026-09-10

Steam was fully exited (`detect_steam_running`: not running, certain, write
allowed). An independent copy of `shortcuts.vdf` was taken *outside* the
repository and outside Steam's timestamped `.bak-*` set:

```text
~/.local/state/steam-desktop-importer/phase10-backups/20260910T170619Z/shortcuts.vdf
```

Baseline fingerprint (same directory, `before.json`):

| Measure | Value |
| --- | --- |
| shortcuts | 982 |
| grid files | 2374 |
| duplicate AppIDs | none |
| VDF SHA-256 | `f7b60eab0966c0e9103caf7877975a73619388f1fa9044ec23a06696391b8900` |

### Import a native app

`org.kde.kate.desktop` is Flatpak on this host, so it is not a native-app
gate. `org.kde.konsole.desktop` is native, importable, unmatched in the
existing VDF, and not already in importer state (`import_status` = New).

Imported through `apply_applications` (the same commit path the GUI uses),
with SteamGridDB artwork prepared into temps then placed after the VDF
commit:

| Field | Observed |
| --- | --- |
| desktop ID | `org.kde.konsole.desktop` |
| unsigned AppID | `3511661831` (first-import candidate, occupied neither in VDF nor state) |
| action | `created` |
| `AppName` | Konsole |
| `Exe` | `"konsole"` |
| `StartDir` | empty (`Path=` is absent; not inferred from the executable parent) |
| `LaunchOptions` | empty |
| `ShortcutPath` | empty |
| `FlatpakAppID` | empty |
| persistent `icon` | absolute `…/grid/3511661831_icon.png` |
| grid files | `3511661831p.png`, `3511661831.png`, `_hero`, `_logo`, `_icon` |
| SteamGridDB | game id `5321125` (verified "Konsole"); all five slots PNG |
| transaction backup | `shortcuts.vdf.bak-20260910T170649Z` |
| state / artwork errors | none |

Unrelated survival after this write (`--ignore-appid 3511661831`):

- **982 / 982** pre-existing shortcuts byte-identical, indices unchanged
- **1** shortcut added (Konsole)
- **5** grid files added, all owned by `3511661831`
- **0** unrelated grid add/remove/change

JPEG and WebP were not available for this SteamGridDB title. A census of the
host `grid/` after the import (magic bytes, no filenames) is PNG 1714, JPEG
573, WebP 6, unrecognised 86. Mixed formats are already present from the
pre-existing library; this native import itself is PNG-only.

### Re-run importer / update the same app

With Steam still closed, the same desktop ID was imported again:

| Field | Observed |
| --- | --- |
| action | `updated` |
| unsigned AppID | `3511661831` (unchanged) |
| mapping AppID | `3511661831` (unchanged) |
| `StartDir` / `LaunchOptions` | still empty |
| transaction backup | `shortcuts.vdf.bak-20260910T170710Z` |

Versus the post-import snapshot: **983 / 983** shortcuts byte-identical, **0**
grid changes. Versus the pre-Konsole baseline: still 982 unrelated identical,
one managed add, five managed grid files.

This re-import happened *before* Steam had opened the new shortcut. That is
a stronger closed-Steam preservation check than the spec's order, but it does
not replace the post-Steam check below.

## Steam UI pass — 2026-09-10

Native Steam was launched. The operator confirmed:

- a non-Steam shortcut named Konsole is present;
- portrait / wide / hero / logo / icon artwork is correct;
- launching it from Steam starts Konsole.

A read-only snapshot taken *while Steam was still running*
(`during-steam-ui.json`) compared to `after-reimport.json`:

| Measure | Result |
| --- | --- |
| shortcut count | still 983; none added or removed |
| indices | unchanged |
| grid files | 2379, **0** add/remove/change |
| third-party shortcuts | **980 / 980 byte-identical** |
| importer-written shortcuts | 3 fingerprints changed (see below) |

The three changed AppIDs are exactly the shortcuts this importer created
(Adobe Firefly, Battle.net, and Konsole). Steam recased two keys on those
entries from the Steam-written schema this importer uses (`AppName`, `Exe`)
to `appname`, `exe`. Logical values were unchanged: same name length, same
exe, empty `StartDir` / `LaunchOptions`, same icon path, same tags.

Konsole additionally had `LastPlayTime` updated from `0` to `1789060834`,
which matches launching it from Steam. The other two importer-owned
entries kept `LastPlayTime = 0`.

Empty working directory and empty arguments survived Steam loading the
shortcut. Grid artwork was not rewritten.

## Post-Steam re-import — 2026-09-10

Steam hung on exit and was killed. After that, `detect_steam_running` was
not running, certain, and write-allowed. The live `shortcuts.vdf` still
parsed (983 entries). Its fingerprint matched `during-steam-ui.json`
exactly — the kill did not tear or further rewrite the file.

Konsole was still present with Steam's recased keys (`appname`, `exe`),
empty `StartDir` / `LaunchOptions`, the absolute `_icon` path, and
`LastPlayTime` `1789060834`.

Re-imported through `apply_applications` while Steam stayed closed:

| Field | Observed |
| --- | --- |
| action | `updated` |
| unsigned AppID | `3511661831` (unchanged) |
| mapping AppID | `3511661831` (unchanged) |
| key casing | existing `appname` / `exe` kept (`set_ci`) |
| `LastPlayTime` | `1789060834` (not reset) |
| transaction backup | `shortcuts.vdf.bak-20260910T172846Z` |
| state / artwork errors | none |

Versus the post-kill snapshot: **983 / 983** shortcuts byte-identical,
**0** grid changes. Versus the pre-Konsole baseline, treating the three
importer-owned AppIDs as managed: **980 / 980** third-party shortcuts
byte-identical, Konsole added, five Konsole grid files added, nothing
removed.

Phase 10 native procedure is complete on this host. TEST-005 (replace a
portrait while Steam is running) is dropped: this importer never writes
while Steam is running.

## Restore

If the live file needs to be rolled back *before* Steam has opened it, copy
the independent backup over `shortcuts.vdf` while Steam is closed. After
Steam has run, prefer Steam's own behaviour plus the timestamped
`shortcuts.vdf.bak-*` next to the live file; the independent copy is the
pre-Konsole 982-entry document.
