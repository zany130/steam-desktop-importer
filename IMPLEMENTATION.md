# IMPLEMENTATION.md

## 1. Project Goal

Build a standalone native Linux desktop application named **Steam Desktop Importer**.

The application discovers installed graphical applications represented by FreeDesktop `.desktop` entries, displays them in a PySide6 GUI, and allows the user to import selected applications into Valve Steam as non-Steam shortcuts.

Optional SteamGridDB integration allows users to search for, preview, and install custom artwork.

The application must prioritize:

1. correct FreeDesktop/XDG behavior;
2. preservation of existing Steam configuration;
3. stable shortcut identity across application updates;
4. explicit separation between native Steam and Flatpak Steam;
5. safe, atomic writes;
6. no silent guessing when multiple Steam installations or accounts exist;
7. explicit labeling of undocumented or reverse-engineered Steam behavior.

This document distinguishes:

- **Official behavior** — specified by FreeDesktop, Flatpak, Qt, Python, etc.
- **Observed Steam behavior** — reproducible current behavior not publicly documented by Valve.
- **Third-party convention** — behavior used by maintained Steam integration tools and accepted by Steam.
- **Implementation choice** — a deliberate rule chosen by Steam Desktop Importer.
- **Experimental behavior** — behavior that still requires real-world validation.

---

## 2. Supported Scope

### 2.1 MVP environment

- Linux
- Python 3.10+
- Qt 6 / PySide6
- Wayland and X11
- Native Steam as the primary supported Steam target
- Flatpak Steam as an **experimental target**
- Native, Flatpak, Snap, and integrated AppImage applications when represented by discoverable `.desktop` entries

The importer itself should initially be distributed as source/virtualenv, native package, portable build, and/or AppImage.

### 2.2 Do not package the importer itself as a Flatpak in the MVP

This is an **implementation choice**, not a claim that a Flatpak build is impossible.

The importer needs broad host filesystem access, Steam configuration access, application discovery, process inspection, and host-command inspection. Doing that from inside a Flatpak sandbox would substantially complicate permissions and architecture.

### 2.3 Out of scope for the MVP

- automatic discovery of unintegrated AppImages
- terminal launcher recreation without a tested terminal adapter
- full FreeDesktop D-Bus activation
- automatic weakening of Flatpak Steam sandbox permissions
- direct `shortcuts.vdf` modification while Steam is running
- artwork, icon, or collection writes while Steam is running
- artwork hot reload while Steam is running (TEST-005)
- silently choosing between multiple plausible Steam accounts
- treating undocumented Steam fields as public/stable Valve APIs

Steam collection/category editing is **Phase 12** (post-MVP). It is not
implemented by writing `shortcuts.vdf` `tags`.

---

## 3. Project Structure

Implemented layout (names that differ from the original sketch are called
out):

```text
steam-desktop-importer/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/steam_desktop_importer/
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py
│   ├── models.py
│   ├── desktop/          # discovery, parser, exec_parser, icons
│   ├── launch/           # adapters + Flatpak Steam wrap (not steam/launch_adapters.py)
│   ├── steam/            # installations, accounts, running (not process.py),
│   │                     # appid, shortcuts, artwork, collections, commits
│   ├── steamgriddb/      # not sgdb/
│   ├── state/
│   └── ui/
├── packaging/linux/      # AppImage desktop file, icon, PyInstaller spec
├── scripts/
└── tests/
    ├── fixtures/
    └── unit/
```

There is no `config.py`. Use `__init__.py`, not `init.py`.

---

## 4. Dependencies

Recommended runtime dependencies:

```text
PySide6
vdf
requests
psutil
pyxdg
```

Prefer standard-library modules for:

- SQLite (`sqlite3`)
- Linux advisory locking (`fcntl`)
- CRC32 (`binascii` or `zlib`)
- filesystem and atomic replacement
- JSON
- URL handling

Maintain a tested dependency range and lock file. Wrap `vdf` behind the Steam shortcut adapter so it can be replaced later.

---

## 5. Data Model

### 5.1 DesktopApplication

```python
@dataclass
class DesktopApplication:
    desktop_id: str
    desktop_path: Path
    name: str
    localized_name: str | None

    raw_exec: str | None
    exec_argv: list[str]

    icon_name: str | None
    icon_source_path: Path | None

    working_directory: str | None
    try_exec: str | None

    terminal: bool
    dbus_activatable: bool

    hidden: bool
    no_display: bool

    only_show_in: list[str]
    not_show_in: list[str]

    source_kind: str
    flatpak_id: str | None
    snap_instance: str | None

    supported_for_import: bool
    unsupported_reason: str | None
```

Possible `source_kind` values:

```text
native
flatpak
snap
appimage
unknown
```

### 5.2 SteamInstallation

```python
@dataclass
class SteamInstallation:
    kind: str            # native | flatpak
    root: Path
    userdata_root: Path
    display_name: str
```

### 5.3 SteamAccount

```python
@dataclass
class SteamAccount:
    steam_id64: str
    account_id32: int
    account_name: str | None
    persona_name: str | None
    userdata_dir: Path
    selection_hints: list[str]
```

The `userdata/<number>/` component is the 32-bit Steam account ID component; do not describe that directory name as a complete textual SteamID3.

---

# 6. Persistent Importer State

Persistent importer state is **required**.

Use SQLite at:

```text
$XDG_STATE_HOME/steam-desktop-importer/state.sqlite3
```

Fallback:

```text
~/.local/state/steam-desktop-importer/state.sqlite3
```

Store at least:

```text
desktop_id
steam_installation_key
steam_account_id32
steam_appid_unsigned
last_known_name
last_known_exec
desktop_path
created_at
updated_at
```

Logical identity:

```text
(steam_installation_key, steam_account_id32, desktop_id)
```

The persisted Steam AppID is authoritative for an already-managed application.

If an application keeps the same desktop ID but its `Name=` or `Exec=` changes, update the existing managed shortcut and **retain its Steam AppID**.

If a desktop ID itself changes because a desktop file is renamed or moved, offer a manual relink/migration workflow rather than guessing.

---

# 7. FreeDesktop/XDG Discovery

## 7.1 XDG roots

Official XDG behavior:

- `$XDG_DATA_HOME`
  - default: `~/.local/share`
- `$XDG_DATA_DIRS`
  - default: `/usr/local/share:/usr/share`
  - preserve configured order exactly

Discover entries under:

```text
<xdg-data-root>/applications/
```

Do not merge `/usr/local/share:/usr/share` into an explicitly configured `$XDG_DATA_DIRS`.

## 7.2 Supplemental provider paths

Inspect these when they are not already reachable through XDG configuration:

```text
~/.local/share/flatpak/exports/share/applications
/var/lib/flatpak/exports/share/applications
/var/lib/snapd/desktop/applications
```

Treat them as provider-specific discovery sources, not replacements for XDG semantics.

## 7.3 Desktop-file ID

Do not key applications by basename alone.

Derive the desktop-file ID according to the FreeDesktop specification from the path relative to the relevant `applications/` root.

Conceptually:

```text
applications/vendor/app.desktop
→ vendor-app.desktop
```

## 7.4 Precedence and masking

The first matching desktop ID in precedence order wins, unless the higher-priority entry masks that ID.

Pseudo-code:

```python
resolved = {}
masked = set()

for root in ordered_roots:
    for file in recursive_desktop_files(root):
        desktop_id = calculate_desktop_id(root, file)

        if desktop_id in resolved or desktop_id in masked:
            continue

        entry = parse_desktop_entry(file)

        if entry.hidden:
            masked.add(desktop_id)
            continue

        resolved[desktop_id] = entry
```

Never scan high-priority entries first and later overwrite them unconditionally with lower-priority entries.

---

# 8. Desktop Entry Parsing

Default `ConfigParser` semantics are not the Desktop Entry specification. Default `shlex.split()` semantics are not the Desktop Entry `Exec=` specification.

Use:

- PyXDG or an equivalent Desktop Entry-aware library for structure/metadata, plus
- a spec-compliant `Exec=` tokenizer/field-code handler

or an equivalently tested implementation.

Read at least:

```text
Type
Name / localized Name[]
Exec
Icon
Hidden
NoDisplay
TryExec
Path
Terminal
OnlyShowIn
NotShowIn
DBusActivatable
```

Provider-specific metadata such as `X-Flatpak` may also be used.

### Type

Only `Type=Application` is an import candidate.

### Hidden

`Hidden=true` has masking/deletion semantics. It must block lower-priority entries with the same desktop ID.

### NoDisplay

`NoDisplay=true` is a visibility flag, not a deletion mask.

Parse it, hide it from the default UI, and provide a **Show menu-hidden applications** toggle.

### OnlyShowIn / NotShowIn

Preserve the metadata. The UI may use `$XDG_CURRENT_DESKTOP` to indicate normal visibility, but suppressed entries may remain available behind an advanced filter.

### TryExec

If `TryExec` cannot be resolved/executed, mark the entry unavailable and do not silently import a broken shortcut.

### Path

Map Desktop Entry `Path=` to Steam's working-directory concept.

If absent, leave StartDir empty unless a specific launch adapter requires a value.

Do not infer StartDir from the executable's parent directory.

### Terminal

The MVP targets graphical applications.

For `Terminal=true`, mark the entry unsupported for direct import unless a tested terminal adapter exists.

### DBusActivatable

The importer does not initially implement desktop-environment D-Bus activation.

If `DBusActivatable=true`, use a valid tested `Exec=` fallback when available; otherwise mark unsupported for MVP.

---

# 9. `Exec=` Parsing

The Desktop Entry `Exec=` value is **not a shell command**.

Produce structured arguments:

```python
exec_argv: list[str]
```

Do not collapse parsing immediately back into an opaque string.

### Field codes

Handle field codes semantically:

- `%f`, `%F`, `%u`, `%U` — omit when the Steam shortcut is not being launched with a document/URL
- `%%` — literal `%`
- `%c` — localized application name where appropriate
- `%k` — desktop-file location where appropriate
- `%i` — handle according to the Desktop Entry specification rather than blind deletion

Do not implement field-code handling as one blanket regex deletion.

### `env` wrappers

Do not convert:

```text
env VAR=value /usr/bin/app
```

to:

```text
exe=/usr/bin/app
LaunchOptions="VAR=value"
```

That changes semantics.

For native Steam, preserve the wrapper:

```text
exe=/usr/bin/env
arguments=["VAR=value", "/usr/bin/app", ...]
```

or use another explicit launcher that truly sets the environment.

Do not introduce shell features unless an explicit launcher intentionally invokes a shell.

---

# 10. Launch Adapters

Parsing and Steam serialization are separate stages.

## Native Steam → native host application

Use the resolved host executable and parsed arguments.

## Native Steam → host Flatpak

Prefer provider metadata such as `X-Flatpak` when available.

Typical adapter:

```text
exe=/usr/bin/flatpak
arguments=["run", ...validated flags..., "org.example.App"]
```

Do not assume the final token of every Flatpak-exported `Exec=` is always the app ID.

## Native Steam → Snap

Use the exported desktop-launch semantics. Do not invent a universal Snap executable path when the desktop entry already provides one.

## Native Steam → integrated AppImage

If the exported desktop entry points to a persistent AppImage path, treat it as a normal executable.

Unintegrated AppImages are outside automatic MVP discovery.

---

# 11. Flatpak Steam Target — EXPERIMENTAL

Flatpak Steam is not native Steam with a different data directory.

Its Steam process runs inside a sandbox.

A raw host path such as:

```text
/usr/bin/foo
```

must not be assumed to work from inside Flatpak Steam.

The commonly used host escape mechanism is:

```text
flatpak-spawn --host ...
```

but this requires access to the:

```text
org.freedesktop.Flatpak
```

D-Bus interface.

Stock Flathub Steam does not currently grant that permission by default.

When Flatpak Steam is selected:

1. determine whether the required permission appears to be available;
2. if host launching is required and permission is absent:
   - explain the limitation;
   - provide the manual override command the user may choose to run;
   - **do not silently grant the permission**;
3. label host launching experimental;
4. require integration tests for native host apps, host Flatpaks, Snaps, and AppImages.

A likely adapter is:

```text
flatpak-spawn --host <host command> <args...>
```

but its exact Steam shortcut representation must remain integration-tested rather than being described as an official Steam contract.

---

# 12. Steam Installation Discovery

### Native candidates

Probe and resolve known native locations around:

```text
~/.local/share/Steam
~/.steam/steam
```

Validate by expected Steam structure rather than existence alone.

### Flatpak candidates

Probe validated Steam data roots below:

```text
~/.var/app/com.valvesoftware.Steam/
```

including canonical and compatibility locations. Resolve symlinks.

### Multiple installations

Never silently use the first path that exists.

If native and Flatpak Steam both exist:

- show both;
- remember the user's chosen default;
- keep state mappings installation-specific.

---

# 13. Steam Account Discovery

Steam account-selection files are not a stable public Valve API.

Do not require any single field such as:

```text
MostRecent
AutoLogin
Timestamp
AutoLoginUser
```

to exist forever.

First enumerate viable accounts under:

```text
<steam_root>/userdata/<account_id32>/
```

Then use available data only as **selection hints**, including where present:

- `registry.vdf` / `AutoLoginUser`
- `loginusers.vdf`
- `AutoLogin`
- `MostRecent`
- timestamps
- account names
- matching userdata directories

Selection policy:

- exactly one valid account → auto-select;
- multiple valid accounts → rank/preselect using hints, but show an account-selection dialog and require confirmation.

Never write to an account solely because it has the newest timestamp.

Persist the selected account per Steam installation.

---

# 14. Steam Running Detection

For MVP shortcut modification, Steam must be fully closed.

This is based on current observed behavior that direct external `shortcuts.vdf` edits can be overwritten by Steam's in-memory shortcut state.

Use conservative process detection based on more than only `name == "steam"` where practical:

- process name
- executable path
- command line
- native/Flatpak indicators

Recheck Steam:

- when import begins;
- immediately before atomic replacement.

A conservative false positive is preferable to writing while Steam is running.

---

# 15. Binary `shortcuts.vdf`

Use binary Valve KeyValues through a dedicated adapter.

When editing an existing file:

- preserve unknown fields;
- preserve existing entries;
- preserve field casing where possible;
- do not normalize unrelated shortcuts;
- do not renumber existing entries unnecessarily.

For newly generated entries, use a schema locked to tested current fixtures.

Logical fields include:

```text
appid
app name
exe
StartDir
icon
ShortcutPath
LaunchOptions
IsHidden
AllowDesktopConfig
AllowOverlay
openvr
Devkit
DevkitGameID
DevkitOverrideAppID
LastPlayTime
FlatpakAppID
SortAs
tags
```

Do not present undocumented casing/field details as a public Valve API.

Until tested:

```text
ShortcutPath = ""
FlatpakAppID = ""
```

For StartDir:

```text
StartDir = Desktop Entry Path=
```

or empty if `Path=` is absent.

For new shortcuts:

```text
tags = {}
```

This is not collection creation.

---

# 16. Steam Shortcut Identity and AppIDs

Do **not** claim CRC32 is Steam's current native AppID algorithm.

Current observed Steam shortcut creation does not reliably match the common deterministic CRC formulas used by third-party tools.

For a previously managed application, the persisted AppID in importer state is authoritative.

### First import

For a new unmanaged desktop ID, generate a deterministic high-bit 32-bit candidate using an importer-owned namespace.

Recommended seed:

```text
"steam-desktop-importer\0" + desktop_id
```

Example:

```python
seed = ("steam-desktop-importer\0" + desktop_id).encode("utf-8")
candidate = crc32(seed) | 0x80000000
candidate &= 0xFFFFFFFF
```

This is an **implementation choice**, not Steam's own algorithm.

Benefits:

- stable if importer state is temporarily lost;
- independent of normal `Name=` changes;
- independent of normal `Exec=` package updates;
- easy to reproduce.

### Collision handling

Before assignment:

1. load every existing shortcut AppID as unsigned uint32;
2. if the candidate belongs to another shortcut, derive another deterministic candidate, e.g. with:
   ```text
   "\0collision:1"
   ```
3. repeat until unused;
4. persist the final result.

Never overwrite an unrelated shortcut because of a CRC collision.

### Signed VDF representation

Keep the logical ID internally as unsigned uint32.

Convert only at serialization:

```python
def uint32_to_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000
```

### Derived 64-bit game ID

Where a Steam launch/game-ID context needs it:

```python
game_id_64 = (appid_unsigned << 32) | 0x02000000
```

Use this for `rungameid`-type contexts, **not** normal grid artwork naming.

---

# 17. Existing Shortcut Detection and Update

If importer state maps:

```text
installation + account + desktop_id → appid
```

then:

- locate that AppID in `shortcuts.vdf`;
- update the existing entry;
- retain AppID;
- preserve unknown existing fields.

If state is missing, `(name, exe, launch options)` may be used only as a heuristic.

Do not automatically take ownership of a manually-created Steam shortcut solely because `(appname, exe)` matches.

Offer explicit relinking when a likely existing shortcut is found.


# 18. Safe `shortcuts.vdf` Transactions

This is a critical requirement.

## 18.1 Importer lock

Acquire a per-installation/per-account advisory lock before read-modify-write.

Hold it through commit.

This protects against multiple importer instances.

It does not lock Steam itself.

## 18.2 Backup

Before replacing the live file:

- create a timestamped or versioned backup;
- avoid filename collisions;
- retain a bounded backup history.

## 18.3 Atomic/crash-durable write

Use a temporary file in the **same target directory**.

Required sequence:

```text
1. read current shortcuts.vdf
2. build updated structure in memory
3. serialize to same-directory temp file
4. flush temp file
5. fsync temp file
6. reopen and parse temp file
7. verify expected structure
8. create backup of current live file
9. recheck Steam is closed
10. os.replace(temp, shortcuts.vdf)
11. fsync the parent config directory
12. release importer lock
```

If validation fails, never replace the live file.

## 18.4 Artwork transaction ordering

Preferred MVP order:

```text
prepare/validate artwork in temporary locations
prepare/validate VDF
commit VDF
commit final artwork files
```

If artwork commit later fails, the shortcut remains valid without custom artwork and the UI should offer an artwork retry.

---

# 19. Artwork Identity and Paths

Grid directory:

```text
<userdata_dir>/config/grid/
```

Create it if required.

## 19.1 Canonical artwork ID

Use the shortcut's **unsigned 32-bit AppID**:

```python
grid_id = appid_unsigned & 0xFFFFFFFF
```

Do not use the derived 64-bit game ID.

## 19.2 Current filename conventions

```text
Portrait:
<grid_id>p.<ext>

Wide:
<grid_id>.<ext>

Hero:
<grid_id>_hero.<ext>

Logo:
<grid_id>_logo.<ext>

Icon asset:
<grid_id>_icon.<ext>
```

Treat these as current observed conventions, not public Valve API guarantees.

## 19.3 Dimensions

Common SteamGridDB artwork includes, for example:

- portrait 600×900 and related portrait sizes;
- wide 460×215, 920×430, etc.;
- hero 1920×620 and other supported hero sizes.

Do not describe one exact resolution as Steam-mandated.

---

# 20. Shortcut Icons

Artwork files and shortcut icon assignment are separate concerns.

When a custom icon is selected:

1. save it persistently;
2. write its absolute persistent path into the shortcut's `icon` field.

Possible persistent locations:

```text
<userdata_dir>/config/grid/<grid_id>_icon.<ext>
```

or:

```text
$XDG_DATA_HOME/steam-desktop-importer/icons/
```

Do not assume that merely creating `<grid_id>_icon.png` automatically assigns the shortcut icon.

---

# 21. SteamGridDB Client

Base:

```text
https://www.steamgriddb.com/api/v2
```

Authentication:

```http
Authorization: Bearer <API_KEY>
```

Never log the API key.

Support environment-variable configuration:

```text
SGDB_API_KEY
```

If persistence is implemented, store secrets outside source code and protect them appropriately.

## 21.1 Search

Search the original display name first.

Optional cleaned queries may remove obvious packaging suffixes, but that is a heuristic.

Allow user editing of the query.

## 21.2 Response handling

Validate:

- HTTP status;
- API success field when present;
- `data`;
- asset URLs;
- empty/no-result responses.

URL-encode path and query values correctly.

## 21.3 Networking

Use:

- connect timeout;
- read timeout;
- bounded retry;
- transient-error retry;
- 429 handling;
- `Retry-After` when supplied;
- session caching/deduplication.

Do not hardcode `0.35s` as though it were an official rate limit.

---

# 22. Artwork Download and Image Validation

Never stream directly over the final destination.

Download to a temporary path first.

Validate:

- HTTP success;
- size limits;
- timeout;
- obvious non-image responses;
- decodability where appropriate.

Static images may preserve their Steam-compatible actual extension or be normalized by decoding/re-encoding.

## 22.1 WebP / animated WebP

SteamGridDB currently documents a Steam compatibility technique in which WebP artwork may be saved using a `.png` or `.jpg` filename while preserving the WebP payload.

Therefore:

- extension/content mismatch is not automatically invalid in this specific documented case;
- animated WebP should not be transcoded to a static PNG if animation should be preserved;
- preserve the WebP payload when using the documented compatibility rename;
- do not claim knowledge of Steam's exact internal decoder unless directly established.

---

# 23. Supported SteamGridDB Artwork Types

Support at least:

- portrait grids;
- wide grids;
- heroes;
- logos.

Icons may be offered separately because they also require persistent shortcut-icon assignment.

Suggested logical API:

```python
search_games(query)
get_grids(game_id, dimensions=None, filters=None)
get_heroes(game_id, filters=None)
get_logos(game_id, filters=None)
get_icons(game_id, filters=None)
download_asset(asset, temp_path)
```

Listing requests send SteamGridDB's documented query params. Defaults match
the API and Steam ROM Manager: `types=static`, `nsfw=false`, `humor=false`,
`epilepsy=false`. The artwork dialog and Settings can opt into animated,
NSFW, joke, and epilepsy artwork, and optionally limit grids to one style.

---

# 24. Collections / Categories

Steam library collections are **Phase 12**, after the native-Steam MVP.

Do **not** present `shortcuts.vdf` `tags` as collection creation. For new
shortcuts:

```text
tags = {}
```

Existing `tags` are still preserved on unrelated entries.

Current clients store collections in userdata cloud storage:

```text
<userdata>/<account_id32>/config/cloudstorage/cloud-storage-namespace-1.json
```

with an index in `cloud-storage-namespaces.json`. Each live collection is a
`user-collections.<id>` record whose `value` is a JSON string
`{id, name, added, removed}`. `added` holds unsigned 32-bit AppIDs, including
high-bit non-Steam IDs. This is observed behavior, not a public Valve API.
See `docs/PHASE12_COLLECTIONS.md`.

Rules for this importer:

- Steam must be closed (same gate as `shortcuts.vdf`).
- Replace the namespace JSON (and the namespaces index) only through
  `steam/collection_commit.py`, with backup, parse-back, and `os.replace`.
- Do not write `localconfig.vdf`.
- Do not add imports to Steam's `hidden` collection or Dynamic
  Collections (`filterSpec`).
- `from-tag-*` store-tag collections are assignable; the UI labels them
  `(tag collection)`.
- Do not create a collection unless the user typed a name or checked an
  existing one.
- Collection-write failures must not roll back a successful shortcut commit.
- New collections use an `sdi-` id prefix.

Treat collection writes as **experimental observed behavior**, not a public
Valve API. Mutations still require Steam closed.

---

# 25. UI Architecture

## 25.1 Main window

Top controls:

- search;
- Steam installation selector;
- Steam account selector;
- Steam status indicator;
- refresh;
- settings.

Main view columns:

- selection;
- icon;
- application name;
- source kind;
- command summary;
- desktop-file ID;
- visibility/support status;
- Steam import status.

Suggested statuses:

```text
New
Imported
Changed
Possible Existing Match
Unavailable
Unsupported
```

## 25.2 Filters

Support:

- text search;
- source type;
- imported/not imported;
- show `NoDisplay`;
- show unsupported;
- current-desktop visibility.

## 25.3 Model/view

Prefer:

```text
QTableView + model + proxy model
```

over a large `QTableWidget`, unless measurements show the simpler widget is sufficient.

## 25.4 Worker model

Do not perform blocking HTTP calls on the GUI thread.

Use `QThreadPool`/`QRunnable` or an equivalent worker model for:

- desktop scanning;
- SteamGridDB requests;
- preview downloads.

Keep VDF mutation serialized and safety-controlled.

---

# 26. Import Workflow

For each selected application:

1. confirm the desktop entry still exists;
2. validate support;
3. select/confirm Steam installation;
4. select/confirm Steam account;
5. resolve source-specific launch adapter;
6. retrieve or allocate stable Steam AppID;
7. optionally select artwork;
8. prepare temporary VDF/artwork;
9. verify Steam is closed;
10. commit safe VDF transaction;
11. commit importer state;
12. commit final artwork;
13. report any partial artwork failure.

If Steam is running:

```text
block VDF import
```

If no SteamGridDB key exists:

```text
allow shortcut-only import
```

---

# 27. State/VDF Recovery Semantics

The state database and VDF cannot be committed atomically as one transaction.

Recommended order:

```text
1. prepare mapping in memory
2. commit validated VDF
3. commit state mapping
```

If the program fails after VDF commit but before state commit:

- reconcile on next launch using AppID and launch-signature heuristics;
- offer a relink action;
- do not silently duplicate the shortcut.

A small operation journal may be added if needed.

---

# 28. Steam Must Be Closed

This importer never mutates Steam userdata while Steam is running.

That includes:

- `shortcuts.vdf`;
- `config/grid/` artwork and icons;
- collection cloud-storage JSON.

Steam must be fully exited before Import or Relink. The running probe may
be overridden only when it is a **false positive** and the user has already
confirmed Steam is gone. That override is not a license to write while
Steam is actually open.

Artwork replacement while Steam is running (TEST-005 / hot reload) is out
of scope. It is theoretically possible; this app will not do it.

---

# 29. Logging

Never log:

- SteamGridDB API key;
- unnecessary tokens;
- raw full environment dumps.

Useful debug fields:

- desktop ID;
- source type;
- Steam installation type;
- account ID32;
- AppID unsigned;
- transaction stage;
- validation error;
- HTTP status;
- artwork type.

Debug modes may expose raw desktop/VDF fields after secrets are removed.

---

# 30. Error Handling

Mutation-related failures must be explicit.

Examples:

- Steam running;
- ambiguous account;
- unreadable or unparsable VDF;
- collision failure;
- backup failure;
- temp validation failure;
- atomic replace failure;
- state DB failure;
- artwork failure;
- Flatpak Steam missing host permission;
- desktop entry disappeared during import.

Do not continue after a failed safety prerequisite.

---

# 31. Implementation Phases

## Phase 0 — Fixtures and format characterization

Before GUI work:

- collect representative `.desktop` fixtures;
- capture current `shortcuts.vdf` fixtures;
- create XDG precedence fixtures;
- create Steam account fixtures;
- document one current native Steam test environment.

Goal: most parsing/writing tests run without modifying a live Steam account.

## Phase 1 — XDG discovery + Desktop Entry parser

Implement:

- XDG ordered roots;
- supplemental provider roots;
- desktop IDs;
- Hidden masking;
- NoDisplay;
- localization;
- TryExec;
- Path;
- Terminal;
- OnlyShowIn/NotShowIn;
- DBusActivatable modeling;
- `Exec=` tokenizer;
- field codes.

Fixtures must include:

- user/system override;
- Hidden mask;
- nested desktop ID;
- `%u`, `%U`, `%f`, `%F`;
- `%%`;
- `%c`;
- `%k`;
- quoting/escaping;
- `env VAR=value`;
- `Path=`;
- missing `TryExec`.

## Phase 2 — Launch adapters

Implement native-Steam adapters for:

- native apps;
- Flatpaks;
- Snaps;
- integrated AppImages.

Verify command vectors without writing Steam.

## Phase 3 — Basic GUI

Implement:

- model/view table;
- filters;
- selection;
- source/status badges;
- install/account selectors.

Verify responsiveness on large app sets.

## Phase 4 — Steam install/account discovery

Implement:

- native Steam enumeration;
- Flatpak Steam enumeration;
- symlink resolution;
- account enumeration;
- hint-based preselection;
- explicit account UI.

Test:

- one install;
- native + Flatpak;
- one account;
- several accounts;
- missing historical fields.

## Phase 5 — Persistent state and AppID allocation

Implement:

- SQLite store;
- desktop-ID mapping;
- deterministic first-import candidate;
- collision resolution;
- signed/unsigned conversion;
- derived 64-bit helper.

Verify:

- Exec change preserves managed AppID;
- Name change preserves managed AppID;
- simulated collision resolves safely;
- account/install mappings remain separate.

## Phase 6 — VDF read/update

Implement:

- binary load;
- preservation of existing fields;
- update by AppID;
- new-entry creation from tested fixture schema;
- possible-match heuristics.

Verify parse → update → serialize → parse round trip.

## Phase 7 — Safe VDF commit

Implement:

- importer lock;
- Steam detection;
- backup;
- same-directory temp;
- fsync;
- parse-back validation;
- final Steam recheck;
- `os.replace`;
- parent-directory fsync.

Failure-injection tests:

- crash after temp serialization;
- validation failure;
- crash before replace;
- replace failure;
- concurrent importer;
- Steam starts during transaction.

Do not enable live writes until this phase passes.

## Phase 8 — SteamGridDB

Implement:

- auth;
- search;
- grids;
- heroes;
- logos;
- icons;
- timeouts;
- retries;
- 429;
- validation;
- caching.

Test:

- valid response;
- no results;
- 401;
- 404;
- 429;
- timeout;
- non-image response.

## Phase 9 — Artwork UI

Implement:

- editable search;
- previews;
- selection;
- skip;
- unsigned 32-bit naming;
- persistent icon path;
- WebP compatibility behavior;
- atomic final placement.

Verify on real Steam:

- portrait;
- wide;
- hero;
- logo;
- icon;
- PNG;
- JPEG;
- WebP where available.

## Phase 10 — Native Steam release gate

Real-world procedure:

1. fully exit native Steam;
2. import a native app;
3. launch Steam;
4. verify shortcut;
5. launch app;
6. verify arguments;
7. verify working directory where relevant;
8. verify artwork;
9. verify icon;
10. exit Steam;
11. re-run importer;
12. update the same app;
13. confirm same AppID remains;
14. confirm unrelated shortcuts remain unchanged.

Native Steam is the MVP release target.

## Phase 11 — Flatpak Steam experimental adapter

Implemented against fixtures. Shortcuts imported into Flatpak Steam are
wrapped with `flatpak-spawn --host`. A Settings flag (default on) can disable
the wrap. Permission to `org.freedesktop.Flatpak` is probed read-only; the
manual override command is shown and **never executed**.

Live TEST-001 (stock vs granted Flathub Steam, host apps / Flatpaks / Snaps /
AppImages, working directory, env wrapper, artwork, icon) still needs
Environment B. Until that matrix passes, do not advertise full Flatpak Steam
support.

## Phase 12 — Steam collections

Optional membership in Steam library collections after a successful shortcut
commit. Native Steam, Steam closed. Cloud-storage JSON only; not `tags`.

1. Read `cloud-storage-namespace-*.json` / `cloud-storage-namespaces.json`.
2. Offer existing assignable collections; never invent one by default.
3. Add the new unsigned 32-bit AppID to `added`.
4. Atomic replace with backup; do not write `localconfig.vdf`.
5. Do not treat opening Steam to inspect the result as a write. All
   mutations happen with Steam closed; there is no live-Steam edit path.

---

# 32. MUST TEST Items

## TEST-001 — Flatpak Steam host launching

Target:

```text
current Flathub com.valvesoftware.Steam
```

Procedure:

1. verify `org.freedesktop.Flatpak` permission is absent;
2. create a valid host-launch shortcut using `flatpak-spawn --host`;
3. attempt launch and record logs;
4. manually grant:
   ```text
   flatpak override --user --talk-name=org.freedesktop.Flatpak com.valvesoftware.Steam
   ```
5. restart Steam;
6. launch again;
7. record behavior.

The importer must never auto-grant this permission.

## TEST-002 — Linux account-selection hints

With two real Steam accounts:

1. log into A and fully exit;
2. capture `registry.vdf`, `loginusers.vdf`, userdata metadata;
3. log into B and fully exit;
4. capture again;
5. compare:
   - `AutoLoginUser`;
   - `AutoLogin`;
   - `MostRecent` if present;
   - timestamps.

Goal: determine useful current hints.

Regardless of result, keep explicit selection fallback for multiple accounts.

## TEST-003 — `FlatpakAppID`

Create A/B shortcuts with identical valid launch commands.

A:

```text
FlatpakAppID=""
```

B:

```text
FlatpakAppID="org.example.ValidID"
```

Test separately under native and Flatpak Steam.

Observe launch/UI/metadata behavior.

Until proven useful:

```text
FlatpakAppID=""
```

## TEST-004 — `ShortcutPath`

Create valid otherwise-identical shortcuts:

A:

```text
ShortcutPath=""
```

B:

```text
ShortcutPath="/path/to/application.desktop"
```

Observe current Steam behavior.

Until a benefit is demonstrated:

```text
ShortcutPath=""
```

## TEST-005 — Artwork hot reload

**Dropped.** Replacing grid files while Steam is running is out of scope.
This importer only writes with Steam fully exited.

---

# 33. Developer Debug Commands

Provide project-native debug tooling rather than ad-hoc scripts that guess the first userdata directory.

Suggested commands:

```text
steam-desktop-importer debug dump-shortcuts \
    --steam-installation <id> \
    --account <account_id32>
```

Show parsed VDF without mutation.

```text
steam-desktop-importer debug identity <desktop_id>
```

Show:

- desktop ID;
- Steam installation;
- account;
- persisted unsigned AppID;
- signed VDF representation;
- derived 64-bit game ID;
- current Name;
- current Exec.

```text
steam-desktop-importer debug desktop-entry /path/to/file.desktop
```

Show:

- desktop ID;
- raw Exec;
- parsed argv;
- field-code expansion;
- source kind;
- visibility flags;
- support status.

---

# 34. Release Safety Requirements

Do not release MVP until:

- XDG precedence tests pass;
- Hidden masking tests pass;
- Exec grammar fixtures pass;
- env-wrapper tests pass;
- persistent identity/AppID tests pass;
- collision tests pass;
- VDF round-trip fixtures pass;
- atomic-write failure-injection tests pass;
- native Steam end-to-end import passes;
- unrelated existing shortcuts survive repeated imports;
- backups are recoverable;
- unsigned 32-bit artwork naming is confirmed;
- SteamGridDB failures cannot corrupt Steam state.

Flatpak Steam may remain experimental.

---

# 35. Non-Negotiable Implementation Rules

1. Do not treat `.desktop` files as ordinary INI + shell commands.
2. Do not key applications by basename alone.
3. Do not skip `Hidden=true` and then fall back to a lower-priority copy.
4. Do not conflate `NoDisplay=true` with deletion.
5. Do not move `env VAR=value` into ordinary arguments.
6. Do not infer StartDir from the executable parent directory.
7. Do not silently choose the first Steam installation.
8. Do not silently guess among multiple Steam accounts.
9. Do not claim CRC32 is Steam's current native AppID algorithm.
10. Do persist the assigned AppID for managed applications.
11. Do collision-check every new AppID.
12. Do use unsigned 32-bit AppID for grid artwork.
13. Do not use the derived 64-bit game ID as the normal artwork prefix.
14. Do explicitly set a persistent shortcut icon path.
15. Do not modify `shortcuts.vdf`, `config/grid/`, or collection JSON while Steam is running.
16. Do not directly truncate/overwrite the live VDF.
17. Do validate a temporary VDF before replacement.
18. Do fsync the parent directory when maximum Linux crash durability is required.
19. Do preserve unknown existing VDF data whenever possible.
20. Do not present `tags = {}` as collection creation. Collection writes go
    through cloud storage with Steam closed (Phase 12).
21. Do not hardcode a guessed SteamGridDB request delay as an official rate limit.
22. Do not assume file extension must always match payload when SteamGridDB documents a Steam compatibility rename.
23. Do not automatically weaken Flatpak Steam's sandbox.
24. Do not advertise experimental Flatpak Steam behavior as guaranteed.
25. When behavior is undocumented and unresolved, mark it experimental and test it.

---

# 36. Evidence Classification

## Official/specification-backed

High confidence:

- XDG data directory precedence;
- desktop-file ID semantics;
- Hidden;
- NoDisplay;
- TryExec;
- Path;
- OnlyShowIn / NotShowIn;
- Desktop Entry Exec grammar;
- Flatpak sandbox permission model;
- `flatpak-spawn --host` D-Bus requirement;
- Linux file/directory fsync behavior;
- atomic same-filesystem replacement behavior.

## Current observed/reverse-engineered Steam behavior

Strong enough for MVP but not a public Valve contract:

- binary `shortcuts.vdf` editing;
- running Steam can overwrite direct external shortcut-file changes;
- high-bit uint32 non-Steam IDs are accepted;
- grid art uses unsigned 32-bit shortcut AppID;
- derived 64-bit game ID is used in launch/game-ID contexts;
- current grid suffix conventions;
- persistent shortcut icon-path behavior;
- current cloud-storage collection representation.

## Deliberate importer choices

- native Steam is the MVP release target;
- persistent Desktop-ID → Steam-AppID state;
- first-import candidate uses CRC32 of importer namespace + desktop ID;
- deterministic collision suffixing;
- NoDisplay hidden by default but revealable;
- collections written through cloud storage (Phase 12), never via `tags`;
- all Steam userdata writes require Steam closed; no live/hot-reload edits;
- Flatpak Steam remains experimental;
- uncertain VDF fields remain empty.

## Experimental / MUST TEST

- current Linux account-selection hints;
- Flatpak Steam host-launch UX and permissions;
- `FlatpakAppID`;
- `ShortcutPath`.

---

# 37. Final Readiness

This specification is ready to guide implementation of the **native-Steam MVP**.

Experimental behavior must remain explicitly labeled and tested rather than silently converted into assumptions during coding.
