# Phase 0 — Format Characterization

This document records what was **observed** on a real system, so that later
phases can be built against evidence rather than assumption.

Evidence classification follows IMPLEMENTATION.md §36.

> Everything in the "Observed Steam behavior" sections below is
> **current observed behavior from a single machine**, not a public Valve API.
> It is a starting point for fixtures, not a contract. Where a single sample is
> not enough to generalize, that is stated explicitly.

Capture date: 2026-09-09.
Capture host: see [TEST_ENVIRONMENT.md](TEST_ENVIRONMENT.md).

All capture was performed **read-only**. No live Steam file was modified.
The capture tool is `scripts/characterize_shortcuts.py`, which never opens a
file for writing and never prints user content (names, executables, launch
options).

---

## 1. `shortcuts.vdf` — observed structure

Sample: native Steam, one account, **783** existing non-Steam shortcuts.

- Binary Valve KeyValues.
- Exactly one top-level key: `shortcuts`.
- `shortcuts` maps **stringified integer indices** to shortcut objects.
- Indices were contiguous `"0" .. "782"`.

### 1.1 Observed key names and casing

This is the single most important Phase 0 finding, because IMPLEMENTATION.md
§15 lists *logical* field names whose casing does **not** match what is
actually on disk.

| IMPLEMENTATION.md §15 logical name | Observed on-disk key | Observed value type |
| --- | --- | --- |
| `appid` | `appid` | int (signed 32-bit) |
| `app name` | `AppName` | str |
| `exe` | `Exe` | str |
| `StartDir` | `StartDir` | str |
| `icon` | `icon` | str (empty in all 783) |
| `ShortcutPath` | `ShortcutPath` | str (empty in all 783) |
| `LaunchOptions` | `LaunchOptions` | str (empty in 550, set in 233) |
| `IsHidden` | `IsHidden` | int |
| `AllowDesktopConfig` | `AllowDesktopConfig` | int |
| `AllowOverlay` | `AllowOverlay` | int |
| `openvr` | `OpenVR` | int |
| `Devkit` | `Devkit` | int |
| `DevkitGameID` | `DevkitGameID` | str (empty in all 783) |
| `DevkitOverrideAppID` | `DevkitOverrideAppID` | int |
| `LastPlayTime` | `LastPlayTime` | int |
| `FlatpakAppID` | `FlatpakAppID` | str (empty in all 783) |
| `SortAs` | `sortas` | str (empty where present) |
| `tags` | `tags` | dict |

Deviations from the spec's written casing: `app name` → **`AppName`**,
`exe` → **`Exe`**, `openvr` → **`OpenVR`**, `SortAs` → **`sortas`**.

IMPLEMENTATION.md §15 explicitly says "Do not present undocumented
casing/field details as a public Valve API" and "preserve field casing where
possible", so this table is treated as *fixture input*, not as truth. The
Phase 6 adapter must round-trip whatever casing it reads.

Binary KeyValues lookup in Steam is believed to be case-insensitive, which is
the likely reason different writers disagree on casing. **Not verified here.**

### 1.2 Two distinct writers are visible in one file

Two key orderings were present, and they correlate with content:

| Count | `sortas` present | `tags` size | Likely writer |
| --- | --- | --- | --- |
| 550 | yes | 0 | Steam itself |
| 233 | no | 3 | a third-party tool |

Attribution to specific writers is **inference, not evidence**.

Two consequences for the implementation:

1. Entries in a single real file legitimately have **different key sets**.
   §15's "preserve unknown fields" and "do not normalize unrelated shortcuts"
   are not hypothetical; a naive rewrite would damage 783 real entries here.
2. `sortas` is **optional**. Do not add it to entries that lack it.

### 1.3 AppID representation

- Stored as a **signed 32-bit** integer.
- All 783 sampled AppIDs had the **high bit set** when read as unsigned
  (range `0x80000000`–`0xFFFFFFFF`), i.e. all were negative as signed.

This is consistent with IMPLEMENTATION.md §36 ("high-bit uint32 non-Steam IDs
are accepted") and with the signed/unsigned conversion required by §16.

It does **not** establish how Steam itself generates the value. Per §16 and
non-negotiable rule 9, no claim is made that CRC32 is Steam's algorithm.

### 1.4 Fields observed empty everywhere

`icon`, `ShortcutPath`, `DevkitGameID`, and `FlatpakAppID` were empty strings
in all 783 entries.

For `ShortcutPath` and `FlatpakAppID` this is consistent with the "until
tested, leave empty" policy in §15 and with MUST-TEST items TEST-003 and
TEST-004. Those remain **untested and unsettled**.

`icon` being empty everywhere is a property of this sample's writers. It does
not contradict §20, which requires the importer to set an explicit persistent
icon path.

---

## 2. Grid artwork naming — observed

Directory: `<userdata_dir>/config/grid/`.

Sample: 1443 files (985 `.png`, 458 `.jpg`).

Observed filename stems:

| Pattern | Count |
| --- | --- |
| `<id>p` (portrait) | 565 |
| `<id>` (wide) | 225 |
| `<id>_hero` | 226 |
| `<id>_logo` | 220 |
| `<id>_icon` | 207 |

### 2.1 Unsigned 32-bit AppID is confirmed as the artwork key

This directly tests IMPLEMENTATION.md §19.1 and non-negotiable rules 12/13:

- distinct numeric IDs in `grid/`: **565**
- IDs matching a shortcut's **unsigned** 32-bit AppID: **565 / 565**
- IDs exceeding `0xFFFFFFFF` (which would indicate 64-bit game-ID naming):
  **0**
- IDs with the high bit set: 565, all corresponding to a real shortcut

**Conclusion:** on this system, grid artwork is keyed by the unsigned 32-bit
AppID, and the derived 64-bit game ID is *not* used as the artwork prefix.

This is strong single-system evidence for an already-specified rule. It is
still observed behavior, not a Valve guarantee.

---

## 3. Steam account configuration — observed

### 3.1 `<steam_root>/config/loginusers.vdf`

Text KeyValues. Structure:

```text
users/
  <steamid64>/
    AccountName
    PersonaName
    RememberPassword
    WantsOfflineMode
    SkipOfflineModeWarning
    AutoLogin
    Timestamp
```

**`MostRecent` was not present.**

IMPLEMENTATION.md §13 anticipates exactly this: "Do not require any single
field such as `MostRecent` / `AutoLogin` / `Timestamp` / `AutoLoginUser` to
exist forever." This capture is direct evidence that `MostRecent` cannot be
relied on. Account discovery must enumerate `userdata/` first and treat these
fields as optional hints.

### 3.2 `~/.steam/registry.vdf`

Text KeyValues. `AutoLoginUser` exists at:

```text
Registry/HKCU/Software/Valve/Steam/AutoLoginUser
```

Sibling keys observed at that level: `AlreadyRetriedOfflineMode`,
`BrowserViewUnderlaysAllowed`, `CEFGPUBlocklistDisabled`, `CompletedOOBE`,
`CompletedOOBEStage1`, `EnableGamescopeComposer`, `GPUAccelWebViewsV3`,
`GamescopeEnableAppTargetRefreshRate2`, `H264HWAccel`, `ModInstallPath`,
`OverrideBrowserComposerMode`, `Rate`, `SourceModInstallPath`,
`StartupModeTmp`, `StartupModeTmpIsValid`, `language`, `steamglobal`.

`AutoLoginUser` holds an **account name**, not a SteamID. Correlating it to a
`userdata/<account_id32>/` directory therefore requires `loginusers.vdf`.

Note that `registry.vdf` lives at `~/.steam/registry.vdf` — **outside** the
Steam root — while `loginusers.vdf` lives inside `<steam_root>/config/`.

### 3.3 `userdata/<account_id32>/config/`

Observed contents: `cloudstorage`, `compat.vdf`, `grid`, `librarycache`,
`licensecache`, `localconfig.vdf`, `shortcuts.vdf`.

Only one account existed on this machine, so the multi-account selection
policy in §13 and MUST-TEST item TEST-002 **could not be exercised**. Those
remain untested.

---

## 4. Native Steam root layout — observed

```text
~/.local/share/Steam          (real directory)
~/.steam/steam    -> ~/.local/share/Steam    (symlink)
~/.steam/root     -> ~/.local/share/Steam    (symlink)
```

All three resolve to the same directory, so Steam installation discovery
(§12) must resolve symlinks and de-duplicate, or it will offer the same
installation three times.

No Flatpak Steam was installed on this machine, so the Flatpak probe paths in
§12 and everything in §11 / TEST-001 are **unexercised**.

---

## 5. Real-world `XDG_DATA_DIRS` on the capture host

```text
/var/lib/portmaster/bin/exports/share
/var/lib/portmaster/bin/exports/share
/tmp/.mount_cursorhiAngA/usr/share/
/usr/local/share
/usr/share
/var/lib/portmaster/bin/exports/share
/home/zany130/.local/share/flatpak/exports/share
/var/lib/flatpak/exports/share
/usr/local/share
/usr/share
```

`XDG_DATA_HOME` was unset (so the `~/.local/share` default applies).

Three findings that shaped the Phase 1 discovery implementation:

1. **Duplicates occur in the wild.** `/var/lib/portmaster/bin/exports/share`
   appears three times and `/usr/local/share:/usr/share` appears twice.
   Discovery must de-duplicate while preserving *first* occurrence order.
2. **Transient paths occur.** `/tmp/.mount_cursorhiAngA/usr/share/` belongs to
   a running AppImage and will not exist on the next boot. Non-existent roots
   must be skipped without error.
3. **The Flatpak exports paths were already in `XDG_DATA_DIRS`.** The
   supplemental provider paths of §7.2 are therefore correctly skipped as
   already-reachable on this host, which is exactly the condition §7.2
   describes.

---

## 6. What Phase 0 did **not** establish

These remain open and must not be treated as settled:

- Steam's own AppID generation algorithm (§16).
- Whether `ShortcutPath` has any effect (TEST-004).
- Whether `FlatpakAppID` has any effect (TEST-003).
- Multi-account selection hints (TEST-002) — only one account available.
- Artwork hot reload while Steam runs (TEST-005) — **dropped**; out of scope.
- Any Flatpak Steam **live** launch behavior (§11, TEST-001) — not installed;
  host-launch wrapping is fixture-tested.
- Whether binary KeyValues key lookup is genuinely case-insensitive in Steam.
- What `LastPlayTime` should be for a brand-new shortcut. §15 lists the field
  but does not specify a value for new entries. Deferred to Phase 6.
