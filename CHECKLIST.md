# Specification Checklist

Tracks IMPLEMENTATION.md compliance. Updated as phases land.

**Current state: Phases 0–9 complete. 433 tests passing.**
Live `shortcuts.vdf` writes exist only in `steam/commit.py`. SteamGridDB
artwork is placed under a userdata `config/grid/` only through
`steam/artwork.py`. Unit tests use `tmp_path` fake userdata; this host's
live Steam grid is not written here. Live visual checks of portrait, wide,
hero, logo, and icon (PNG/JPEG/WebP) are Phase 10 / TEST-005.

Legend: `[x]` done · `[ ]` not started · `[~]` partial

---

## Phase 0 — Fixtures and format characterization

- [x] Representative `.desktop` fixtures — `tests/fixtures/desktop_entries/`
- [x] Current `shortcuts.vdf` fixtures — `tests/fixtures/shortcuts_vdf/`
- [x] XDG precedence fixtures — `tests/fixtures/desktop_entries/xdg_precedence/`
- [x] Steam account fixtures — `tests/fixtures/steam_config/`
- [x] One current native Steam test environment documented — `docs/TEST_ENVIRONMENT.md`
- [x] Parsing/writing tests run without touching a live Steam account

Characterization was done against a **real** native Steam install, strictly
read-only, using `scripts/characterize_shortcuts.py`. Findings are in
`docs/PHASE0_FORMAT_CHARACTERIZATION.md`. Highlights:

- Real on-disk field casing is `AppName`, `Exe`, `OpenVR`, `sortas` — four keys
  differ from §15's written list. See DEV-1.
- All 783 real AppIDs have the high bit set; stored signed.
- **§19.1 empirically confirmed:** 565 of 565 grid artwork IDs match a
  shortcut's *unsigned* 32-bit AppID; none exceed `0xFFFFFFFF`.
- **`MostRecent` does not exist** in the current `loginusers.vdf`, which
  directly vindicates §13's warning.
- One real file contains two different writers with two different key sets
  (550 with `sortas`, 233 with populated `tags`), so §15's preservation rules
  are load-bearing, not theoretical.

## Phase 1 — XDG discovery + Desktop Entry parser

### §7 Discovery

- [x] `$XDG_DATA_HOME` with `~/.local/share` default; relative value rejected
- [x] `$XDG_DATA_DIRS` with defaults only when unset **or empty**
- [x] Configured order preserved exactly
- [x] Defaults **not** merged into an explicitly configured `$XDG_DATA_DIRS`
- [x] Order-preserving de-duplication (real values contain duplicates)
- [x] Supplemental Flatpak/snapd roots, skipped when already XDG-reachable
- [x] Desktop IDs from the path relative to the root, `/` → `-` (rule 2)
- [x] First-match-wins precedence (§7.4)
- [x] `Hidden=true` masks lower-priority copies (rule 3)
- [x] Non-Application entries still claim their ID
- [x] Never resolve high-priority then overwrite with low-priority

### §8 Entry parsing

- [x] `configparser` not used anywhere
- [x] `Type`, `Name`/`Name[]`, `Exec`, `Icon`, `Hidden`, `NoDisplay`, `TryExec`,
      `Path`, `Terminal`, `OnlyShowIn`, `NotShowIn`, `DBusActivatable`
- [x] Provider metadata: `X-Flatpak`, `X-SnapInstanceName`, `X-AppImage-*`
- [x] Only `Type=Application` is an import candidate
- [x] `NoDisplay` is visibility, not deletion (rule 4)
- [x] `OnlyShowIn`/`NotShowIn` preserved as metadata, not used to filter
- [x] Unresolvable `TryExec` marks the entry unavailable
- [x] `Path=` → working directory; **never** inferred from the exe parent (rule 6)
- [x] `Terminal=true` unsupported pending a tested terminal adapter
- [x] `DBusActivatable` modelled; Exec fallback used when present
- [x] Specification-ordered locale fallback, encoding stripped
- [x] Semicolon lists with `\;` escaping
- [x] Booleans restricted to `true`/`false`, legacy `1`/`0` accepted with a warning

### §9 `Exec=` parsing

- [x] Not treated as a shell command; `shlex` not used (rule 1)
- [x] Structured `exec_argv`, not collapsed back to a string
- [x] Value escapes applied **before** quoting, per specification ordering
- [x] Four-backslash rule and `\\$`
- [x] `%f`, `%F`, `%u`, `%U` omitted when no document is passed
- [x] `%%` is a literal percent and hides no field code
- [x] `%c` localized name, kept as one argument
- [x] `%k` desktop-file location
- [x] `%i` expands to two arguments, or zero without an `Icon`
- [x] Deprecated codes expand to nothing but are reported
- [x] **Not** a blanket regex deletion
- [x] `env VAR=value` preserved verbatim in argv (rule 5)
- [x] No shell features introduced
- [x] Strict grammar first; per-entry compatibility retry only for recognised
      non-standard single-quote patterns, marked `nonstandard_exec` /
      `exec_parse_mode="compat"` (DEV-8)

### §33 Debug commands (partial)

- [x] `debug roots`
- [x] `debug scan`
- [x] `debug desktop-entry <path>`
- [x] `debug launch [desktop-id]` — Phase 2 vectors; never runs or writes
- [x] `debug steam` — Phase 4 installations and accounts, plus §14 running status; read-only
- [x] `debug identity` — Phase 5; never creates state or writes Steam
- [x] `debug dump-shortcuts` — Phase 6; parsed VDF, never writes
- [x] `debug steamgriddb` — Phase 8; search/list only, never writes Steam

## Phase 2 — Launch adapters (§10)

Complete. Command vectors are produced and verified; nothing writes to Steam.
`LaunchVector` carries `exe`, tokenized `arguments`, `start_dir` and the
adapter name. Arguments stay tokenized because Steam stores them as one
`LaunchOptions` string, and quoting that correctly is Phase 6 serialization —
flattening here would destroy the boundaries needed to do it.

- [x] Native — resolved executable plus parsed arguments
- [x] Flatpak — exported command preserved, forwarding scaffolding removed
- [x] Snap — exported launch semantics used as published
- [x] AppImage — persistent path treated as a normal executable
- [x] Unintegrated AppImages refused (§10 puts them outside MVP discovery)
- [x] `env VAR=value` preserved as `exe=env` plus leading arguments (rule 5)
- [x] `StartDir` from `Path=` or empty, never inferred (rule 6)
- [x] Flatpak app ID never assumed to be the final token (§10)
- [x] No universal Snap executable path invented (§10)

Adapters are **preserving by default**. The desktop entry already contains a
working command, so the job is to carry it across faithfully rather than
rebuild it from provider metadata. Rebuilding a Flatpak as `flatpak run <id>`
would silently drop `--nosocket`, `--env` and `--command` flags the package
was published with; `org.example.FlatpakNoMetadata` is the fixture that pins
this, and it also has an argument *after* the app ID so that any "last token
is the ID" shortcut fails loudly.

### DEV-10 — Support determination is two-stage

Phase 1 decides support from the entry; Phase 2 can still refuse. Currently
one adapter-level refusal exists, `appimage_not_integrated`, because whether
an AppImage path is transient is a launch concern rather than a parse one.
`build_launch_vector` also refuses anything Phase 1 already rejected, so the
adapter cannot route around earlier checks.

Consequence to keep in mind for Phase 3: `supported_for_import` is necessary
but not sufficient. The GUI must call the adapter to know an entry is truly
importable. On the capture host 777 entries are supported and all 777 produce
a vector, so the gap is currently empty — but it is real and untested against
a transient AppImage in the wild.

## Phase 3 — Basic GUI (§25)

Complete. `steam-desktop-importer` with no subcommand opens the window.
Import and Relink are enabled only when Steam is closed (or the probe is
certain), an installation and account are selected, and matching rows are
ticked.

- [x] `QTableView` + `QAbstractTableModel` + `QSortFilterProxyModel` (§25.3)
- [x] Columns: selection, icon, name, source, command, desktop ID, status,
      In Steam
- [x] Source and status drawn as badges
- [x] Text search, source type, NoDisplay, unsupported, current-desktop
- [x] Steam installation selector; several start on a placeholder (rule 7)
- [x] Steam account selector; several require the confirmation dialog (rule 8)
- [x] Steam running indicator (§14), used for display and to gate writes
- [x] Live Steam poll while the window is open, plus a re-check on Import/Relink
      (interval configurable in Settings; detection can be overridden with a
      warning for false positives, which also skips the write-time probe)
- [x] Refresh off the GUI thread (`QThreadPool` / `QRunnable`, §25.4)
- [x] Settings control that does not pretend later phases exist
- [x] Desktop-ID collision acknowledgement (session-only in Phase 3; persisted in Phase 5)

### DEV-12 — Import status was `unknown` until Phase 5

Superseded by DEV-14. Phase 3 showed `unknown` rather than `New` because
the store did not exist yet. The column now uses the §25.1 statuses.

### DEV-13 — §14 detection exists; it does not gate writes

Phase 3 needed a status indicator, so `steam/running.py` is implemented now
rather than waiting for Phase 7. A conservative false positive is preferred,
and an unreadable process makes a *negative* result uncertain. **Nothing
consults this result before writing**, because nothing writes.

The GUI honours DEV-10: a row is checkable only when `supported_for_import`
is true *and* `build_launch_vector` succeeds.

Filtering 2 000 synthetic rows is covered by
`test_filtering_two_thousand_rows_stays_responsive`.

## Phase 4 — Steam install/account discovery (§12–§13)

Complete, and entirely read-only.

### §12 Installations

- [x] Native probing around `~/.local/share/Steam` and `~/.steam/{steam,root,Steam}`
- [x] Flatpak probing below `~/.var/app/com.valvesoftware.Steam/`, canonical
      and compatibility locations
- [x] Validation by **structure** — `userdata/` plus `steamapps/` or `config/`
- [x] Symlink resolution and de-duplication
- [x] Never silently uses the first path that exists (rule 7)
- [x] `SteamInstallation.key` for §6's installation-specific state

The capture host is the reason de-duplication is not optional:
`~/.local/share/Steam`, `~/.steam/steam` and `~/.steam/root` all resolve to
one directory, so probing alone offers the same install three times.

Flatpak installations carry `is_experimental` and say so in `display_name`.
Having a fixture does not validate any §11 runtime behaviour.

### §13 Accounts

- [x] `userdata/<account_id32>/` enumerated **first**, as source of truth
- [x] `loginusers.vdf` and `registry.vdf` used as optional hints only
- [x] Accounts absent from `loginusers.vdf` are still offered
- [x] Malformed or missing hint files degrade to "no hints", never an error
- [x] One account auto-selects; several always require confirmation (rule 8)
- [x] Timestamp never decides on its own

`registry.vdf` holds an *account name*, not an ID, so correlating it to a
`userdata/` directory needs `loginusers.vdf`. It also lives outside the Steam
root, which is why `SteamInstallation.registry_path` exists as an additive
field rather than being derived from `root`.

Ranking order is `registry-autologinuser` → `loginusers-mostrecent` →
`loginusers-autologin` → timestamp. The multi-account fixture is adversarial
on purpose: `registry.vdf` names alpha, `AutoLogin=1` names beta, and beta
also has the newer timestamp. Confirmation is required regardless of how the
ranking comes out.

### DEV-11 — `userdata/0` is treated as non-viable

§13 says to enumerate "viable accounts" without defining viability. Steam
creates `userdata/0` as a placeholder rather than as a signed-in account, so
non-positive IDs are excluded; importing into it would write to a directory
that belongs to no one. Non-numeric entries such as `anonymous` are skipped
for the same reason. Recorded because §13 does not spell this out.

Verified on the capture host: 1 installation (3 aliases collapsed), 1 account,
all three hints present, resolved without a prompt.

### Not yet done, and deliberately so

§13 asks for the chosen account to be persisted per installation and §12 for
the chosen installation to be remembered. Phase 5 now stores both.

`§14 Steam running detection` is implemented for the Phase 3 indicator
(DEV-13) and Phase 7 uses it to block a VDF write, including an uncertain
negative.

## Phase 5 — Persistent state and AppID allocation (§6, §16, §25.1)

Complete. The store writes only
`$XDG_STATE_HOME/steam-desktop-importer/state.sqlite3` (fallback
`~/.local/state/...`). GUI and `save_mapping` never allocate-and-insert in
one step: §27 requires the VDF commit first, so mappings are persisted only
after a successful Phase 7 write. Scanning does **not** create mappings.

- [x] SQLite store with the §6 fields and identity
      `(steam_installation_key, steam_account_id32, desktop_id)`
- [x] Deterministic first-import candidate
      (`crc32("steam-desktop-importer\\0" + desktop_id) | 0x80000000`)
- [x] Collision salts `"\\0collision:N"` against occupied AppIDs
- [x] Signed/unsigned conversion and `game_id_64`
- [x] Name / Exec changes keep the persisted AppID
- [x] Install and account mappings stay separate
- [x] Remembered installation and per-install account
- [x] Remembered collision acknowledgements bound to the physical winner and
      colliding-path fingerprint (still global, not per Steam account)
- [x] New / Imported / Changed / Possible Existing Match
- [x] Read-only listing of existing `shortcuts.vdf` identities
- [x] `debug identity <desktop_id>`
- [x] Write guard allowlists only `state/store.py` `mkdir`

### Characterization (read-only, 2026-09-09)

In-memory store; real `shortcuts.vdf` opened `rb` only. The real
`state.sqlite3` did not exist before and was not created. VDF mtime and
size were unchanged.

| Observation | Value |
| --- | --- |
| Resolved desktop entries | 804 |
| Importable | 777 |
| Desktop-ID collisions | 1 |
| Steam install | 1 native (`/var/home/zany130/.local/share/Steam`) |
| Steam account | `120415481` / `zany130` |
| Existing shortcut identities | **961** (Phase 0 recorded 783; added later via Steam ROM Manager) |
| Unique occupied AppIDs | 961, all high-bit |
| Statuses on an empty store | 804 New, 0 Imported/Changed, 0 Possible Existing Match |
| First-import candidates vs occupied | 777 free, **0 collisions** |

Possible Existing Match is 0 because the name+exe heuristic never fires on
this host. 81–94 shortcuts share a desktop `Name=`, but every sampled
`Exe` is a Unifideck launcher or a Proton `.desktop` path, while the
corresponding desktop `Exec` is `xdg-open` or an emulator script. That is
the §17 case: same name is not ownership.

### DEV-14 — Phase 5 may read `shortcuts.vdf`; it must not update it

§16 collision checks and §25.1 Possible Existing Match need existing
shortcut identities. VDF update/serialize is Phase 6. Phase 5 therefore
lists identities read-only (`rb` + `vdf.binary_load`) and takes an explicit
occupied set. An unparsable file is an error, not an empty occupied set.

`Imported` means *managed in importer state*. A successful Phase 7 import
writes the VDF row first, then the mapping; a crash between those steps can
still leave an unmanaged VDF entry (§27).

### Not yet done, and deliberately so

Artwork, SteamGridDB, and native end-to-end against a live Steam account
remain later phases.

## Phase 6 — VDF read/update (§15–§17)

Complete. `ShortcutDocument.dumps()` returns bytes. Replacing a live file
is Phase 7 (`commit_shortcuts`).

- [x] Binary load (`ShortcutDocument.load` / `loads`)
- [x] Byte-identical load → dumps on every loadable fixture
- [x] Preserve unknown fields, key casing, key order, and index holes
- [x] Update by unsigned AppID; retain AppID
- [x] New entries use the Steam-written fixture schema (`STEAM_NEW_ENTRY_KEYS`)
- [x] `LastPlayTime = 0` on new entries (OPEN-6 accepted)
- [x] `ShortcutPath` / `FlatpakAppID` left empty (TEST-003/004)
- [x] Possible-match heuristic (name+exe, optional launch options); never auto-owns
- [x] `debug dump-shortcuts`

### Characterization (read-only, 2026-09-09)

Live `shortcuts.vdf` (961 entries, 354854 bytes) was loaded and
`dumps()`-ed in memory. The result was **byte-identical**. An in-memory
rename left the on-disk mtime, size, and bytes unchanged.

Three writer shapes are present:

| Count | Keys | Notes |
| --- | --- | --- |
| 550 | 18, includes `sortas` | Steam-written (Phase 0) |
| 233 | 17, no `sortas`, populated `tags` | earlier third-party tool |
| 178 | 7: `LaunchOptions`, `StartDir`, `appid`, `appname`, `exe`, `icon`, `tags` | Steam ROM Manager batch; lowercase `appname`/`exe` |

New importer entries still use the 18-key Steam-written fixture schema.
Updates write through the existing key casing, so an SRM row is not
rewritten into the Steam schema.

Indices are `"0"`–`"960"` with no numeric holes; dict order after load is
lexical (`"0"`, `"1"`, `"10"`, …). New entries append `max+1` and do not
reorder.

### Not yet done, and deliberately so

Replacing the live file is Phase 7, implemented on tmp copies and through
the GUI only when Steam is closed.

## Phase 7 — Safe VDF commit (§18, §26–§27)

Complete. The live host `shortcuts.vdf` was **not** used as a write target
during implementation; failure-injection and import tests use tmp directories.

A copy of the current live file (979 entries, 396726 bytes) was committed
in `/tmp` with a rename of one entry: backup bytes matched the original,
the temp copy updated, and the real userdata mtime/size/bytes were unchanged.

- [x] Per-file advisory importer lock (`shortcuts.vdf.lock`, `LOCK_EX | LOCK_NB`)
- [x] Steam-closed gate, including an uncertain probe
- [x] Timestamped backups in the same directory, collision-safe names
- [x] Bounded backup history (`MAX_BACKUPS = 10`)
- [x] Same-directory temp, flush, fsync
- [x] Parse-back validation before replace
- [x] Final Steam recheck
- [x] `os.replace` + parent-directory fsync
- [x] Failure-injection: crash after serialize, validation failure, crash
      before replace, replace failure, concurrent importer, Steam starts
      during the transaction
- [x] Stale-document check (`original_bytes`)
- [x] Import applies New/Changed/Imported; Possible Existing Match is relink
      only
- [x] Mappings persisted only after a successful VDF commit
- [x] Import / Relink buttons gated on Steam closed + selected target;
      status is polled live and re-checked on click unless detection is
      overridden

### Not written, on purpose

- The development host's real `shortcuts.vdf` (961 entries)
- The development host's live `config/grid/` (Phase 9 unit tests use tmp userdata)
- Collections (§24)

## Phase 8 — SteamGridDB client (§21–§23)

Complete. HTTP is mocked in unit tests; no live SteamGridDB key is required
to run the suite. Artwork is downloaded only to a caller-supplied temp path.

- [x] Bearer auth; `SGDB_API_KEY`; optional 0600 config-file key
- [x] `search_games`, `get_grids` / `get_heroes` / `get_logos` / `get_icons`
- [x] Connect and read timeouts
- [x] Bounded retry for 429/5xx with `Retry-After` when present
- [x] No hardcoded 0.35s "official" rate limit (rule 21)
- [x] Validate HTTP status, `success`, `data`, and asset URLs
- [x] Empty `data` is a successful no-result
- [x] Session GET cache / deduplication
- [x] Download to temp; size limit; reject obvious non-images
- [x] WebP payload with a `.png` name is not treated as invalid (rule 22)
- [x] API key never appears in exception text
- [x] Settings field for the key; env var takes precedence
- [x] `debug steamgriddb search|grids|heroes|logos|icons`

Tests: valid response, no results, 401, 404, 429, timeout, non-image.

### Not yet done, and deliberately so

Live SteamGridDB + live Steam visual verification of placed artwork is
Phase 10. This client still does not write `grid/` itself.

## Phase 9 — Artwork UI (§18.4, §19–§20, §22, §25.4, §26)

Complete. Search, preview, and selection run off the GUI thread. Prepared
temps are placed into `<userdata>/config/grid/` *after* a successful VDF
commit. A missing API key keeps import shortcut-only.

- [x] Editable SteamGridDB search, prefilled with the display name
- [x] Previews, per-slot selection, skip, skip remaining, cancel=skip
- [x] Unsigned 32-bit decimal naming (`<id>p`, `<id>`, `_hero`, `_logo`, `_icon`)
- [x] Not the derived 64-bit `game_id` (rule 13)
- [x] Persistent shortcut `icon` is the absolute `_icon` path (§20)
- [x] WebP payload kept under a `.png` filename (rule 22)
- [x] Same-directory temp + `os.replace`; never stream onto the live name
- [x] Order: temps → VDF → state → artwork; artwork failure does not roll back the shortcut
- [x] Combined import still requires Steam closed
- [x] HTTP off the GUI thread; one SteamGridDB client per worker
- [x] Write-guard allowlists `steam/artwork.py` only for `grid/` writes

### Not written, on purpose

- This host's live Steam `config/grid/`
- Collections (§24)
- Artwork-only refresh while Steam is running (§28; later)

## Not started

Phases 10–11. Specifically **not** implemented, as instructed:

- Steam collections/categories (§24, rule 20) — out of scope for MVP
- Any Flatpak permission modification (§11, rule 23)
- Native end-to-end on live Steam (Phase 10) and Flatpak Steam matrix (Phase 11)

---

## Deviations from the specification

### DEV-1 — Observed VDF field casing differs from §15's list

§15 lists `app name`, `exe`, `openvr`, `SortAs`. The real file has `AppName`,
`Exe`, `OpenVR`, `sortas`.

Not treated as a contradiction: §15 calls these "logical fields" and says
explicitly not to present casing as a public Valve API. The observed casing is
recorded in `docs/PHASE0_FORMAT_CHARACTERIZATION.md` §1.1 and pinned by
`test_steam_written_entry_uses_the_observed_key_casing`, so Phase 6 builds
against reality. **No action needed, but Phase 6 must round-trip whatever
casing it reads rather than normalising to either list.**

### DEV-2 — Own Desktop Entry reader instead of PyXDG for structure

§8 offers "PyXDG or an equivalent Desktop Entry-aware library ... **or an
equivalently tested implementation**". This project uses the third option for
entry structure, for three reasons:

1. PyXDG does not apply `string` value escapes (`\s`, `\n`, `\t`, `\r`, `\\`),
   which §9's processing order requires *before* Exec tokenization.
2. §5.1 needs the localized **and** unlocalized name separately; PyXDG's
   accessor returns one resolved value driven by process-global locale state.
3. Tests need to inject a locale explicitly.

PyXDG **is** still a dependency and is used for icon theme lookup in
`desktop/icons.py`, where its index parsing and inheritance handling are worth
having.

### DEV-3 — Additive fields on `DesktopApplication`

§5.1's dataclass is implemented with its exact field names and types, plus
five defaulted additions: `unsupported_code`, `entry_type`, `source_root`,
`field_codes`, `parse_warnings`.

`unsupported_code` is the load-bearing one: §25.1 asks the UI for two distinct
statuses, "Unavailable" and "Unsupported", which the specified free-text
`unsupported_reason` cannot reliably distinguish without string matching.
`AVAILABILITY_CODES` defines the split. The others serve the §33 debug output
and precedence display.

### DEV-4 — Supplemental roots placed last in precedence

§7.2 says to treat Flatpak/snapd export directories as "provider-specific
discovery sources, not replacements for XDG semantics", but does not say where
they sit in precedence order. They are appended **after** all XDG roots, so a
real XDG root can always override them. On the capture host they are already
present in `$XDG_DATA_DIRS` and are correctly skipped as duplicates, so this
choice has no effect there.

### DEV-5 — Unparsable files do not claim their desktop ID

§7.4's pseudo-code assumes parsing always succeeds. When a file fails to
parse, this implementation records the error and lets a lower-priority
readable copy win, rather than letting a broken file mask a working one.
§7.4's masking rule is specifically about `Hidden=true`. Zero parse errors
occurred across 804 real entries.

### DEV-6 — Structural unsupport reported before unavailability

When an entry is both `Terminal=true` and has an unresolvable `TryExec`, the
terminal reason wins. Installing the binary would not make it importable,
whereas the reverse is not true. §8 does not specify an order.

### DEV-8 — Strict-first parsing with a per-entry compatibility retry

Resolves OPEN-1.

The Desktop Entry specification reserves `'` and forbids its use, so a
compliant tokenizer treats it as an ordinary character. Real generated
launchers use it as POSIX shell quoting anyway — 32 of 804 entries on the
capture host. Bottles, for example, writes:

```text
Exec=flatpak run --command=bottles-cli com.usebottles.bottles run -p EzRO -b 'EMU Stuff' -- %u
```

Strict parsing turns `'EMU Stuff'` into `"'EMU"` and `"Stuff'"`, which would
launch the wrong thing.

**The strict FreeDesktop tokenizer always runs first.** Compatibility parsing
is never enabled globally. It is reached only when all three of the following
hold, at which point the strict result is demonstrably wrong:

1. `looks_like_shell_single_quoting()` recognises the pattern;
2. the compatibility tokenizer succeeds;
3. its tokens actually differ from the strict tokens.

The entry is then marked `nonstandard_exec=True` and
`exec_parse_mode="compat"` on `DesktopApplication`, a warning is recorded, and
`debug scan --nonstandard-only` lists exactly these entries.

The compatibility tokenizer is the strict tokenizer plus one change: `'` opens
a literal-quoted region. The double-quote rules, escape handling and field-code
logic are identical, so a compat parse differs from a strict parse in exactly
one respect.

Pattern recognition is deliberately conservative. It requires every single
quote outside a double-quoted region to pair up, each opening quote to sit at
an argument boundary (start, whitespace, or after `=`), and each closing quote
to be followed by whitespace or end the value. Measured against real data,
this correctly separates three classes:

| Class | Example | Outcome |
| --- | --- | --- |
| Shell quoting | `-b 'EMU Stuff'` | 32 entries → **compat**, argv fixed |
| Quotes inside double quotes | `bash -c "'/path/x.sh'"` | 17 entries → **strict**, already correct |
| `'\''` escape idiom | `-c '/bin/svc -o '\''%u'\'''` | 1 entry → **strict**, argv known wrong → refused, see DEV-9 |
| Apostrophes | `don't`, `it's a b's` | → **strict**, a shell parser would corrupt these |

Note the fourth row: `it's a b's` has *balanced* quotes, so a naive balance
check would silently turn it into `its a bs`. The argument-boundary rule is
what prevents that.

Verified on the capture host: 0 entries flipped to compat without their argv
actually changing, and parse errors stayed at 0.

### DEV-9 — Shell quote-escaping makes an entry unsupported

Contains OPEN-7. Follows from DEV-8's fourth class.

DEV-8 left the `'\''` idiom to the strict grammar and reported the damage
through warnings. That is not sufficient: the argv is not merely unusual, it
is **known to be wrong**, and a warning does not stop Phase 2 from importing
it and creating a broken shortcut.

The real entry, `com.stremio.Service` on the capture host, reduces to:

```text
Exec=... --command=sh com.example.Service -c '/usr/bin/service -o '\''%u'\'''
```

A shell reads the `-c` operand as exactly one argument,
`/usr/bin/service -o '%u'`. Both tokenizers disagree, in different ways:

| Tokenizer | Result | Wrong how |
| --- | --- | --- |
| strict | `... -c` `'/usr/bin/service` `-o` | splits inside the intended quoting, drops the tail, keeps a stray quote |
| compat | `... -c` `/usr/bin/service -o \%u\` | literal backslashes where the quotes belong |

**Decision: refuse the entry.** `uses_shell_quote_escaping()` detects a
backslash immediately preceding `'` outside any double-quoted region.
`ExecParseResult.ambiguous_quoting` carries the flag and
`UnsupportedCode.EXEC_AMBIGUOUS_QUOTING` makes the entry unimportable. It is
checked *before* `Terminal=` and `TryExec=` so the reported reason is the real
one; a "binary not found" message would send the user to fix the wrong thing.
The entry stays **available** — nothing about the system needs repairing —
and is reported as unsupported.

#### Why not parse it

Compat currently deviates from the specification in exactly one respect: `'`
opens a literal region. Handling this idiom needs a second deviation, POSIX
unquoted-backslash escaping, and there is **one** real sample to validate it
against. Guessing at shell semantics on n=1 is what the non-negotiable rules
exist to prevent. Refusing is reversible and cannot produce a wrong launch.

Adding the parser later is a contained change: extend the compat tokenizer
with unquoted `\X` → literal `X`, extend `looks_like_shell_single_quoting()`
to accept quotes adjacent to an escaped quote, and drop the flag. It needs
more real samples first.

Verified on the capture host: exactly 1 of 804 entries is refused, the 32
compat entries are unaffected, and importable moved 778 → 777.

### DEV-7 — Lenient boolean handling

Only `true`/`false` are valid per the specification. Legacy `1`/`0` are
accepted with a warning, since they were valid in an older revision and still
appear in the wild. Anything else falls back to the key's default with a
warning, matching GLib. See OPEN-4 for why the fallback direction is not
always safe.

### DEV-14 — Phase 5 may read `shortcuts.vdf`; it must not update it

See the Phase 5 section. Collision occupancy and Possible Existing Match
need existing identities; VDF update is Phase 6. `Imported` means managed
in importer state until Phase 6 can confirm the VDF row.

---

## Open issues

These need a decision. They are **not** resolved unilaterally; current
behaviour is pinned by `tests/unit/test_open_issues.py` so any change is
deliberate.

| Issue | Severity | Status |
| --- | --- | --- |
| OPEN-1 single quotes | high | **resolved** → DEV-8 |
| OPEN-2 desktop ID collisions | medium | **resolved** — implemented |
| OPEN-2a does a collision block import? | medium | **resolved** — implemented |
| OPEN-3 Flatpak file-forwarding markers | medium | **resolved** — implemented + measured |
| OPEN-4 malformed booleans | low | **open** — conservative `Hidden` policy needed before release |
| OPEN-5 "missing TryExec" ambiguity | low | **resolved** — both readings covered |
| OPEN-6 `LastPlayTime` for new shortcuts | low | **accepted** — `0` |
| OPEN-7 shell quote-escaping in `Exec=` | medium | **contained** → DEV-9; entry refused, parser optional |

### ~~OPEN-1 — Single quotes~~

**Resolved. See DEV-8.**

### ~~OPEN-2 — Desktop IDs are not unique~~

**Resolved 2026-09-09, including OPEN-2a. Implemented and covered by tests.**

Kept in full below because the resolution is a deliberate design decision
about §6's identity model, not a bug fix, and the reasoning needs to stay
available to Phase 5.

The FreeDesktop scheme replaces `/` with `-` and does not escape existing
dashes, so `vendor/app.desktop` and `vendor-app.desktop` in the same root
produce the same ID. Observed for real on the capture host
(`~/.local/share/applications/ons/dev.vencord.Vesktop.desktop` versus
`~/.local/share/applications/ons-dev.vencord.Vesktop.desktop`).

§7.4 resolves the display side deterministically. The risk is narrower than it
first appears: discovery only ever resolves one file per ID, so at any single
moment the state row is unambiguous. The danger is **over time** — if the
winning file is removed, the shadowed file inherits the winner's Steam AppID
and the user's shortcut silently starts launching a different application.

#### Resolution

1. §6's state key is **unchanged**: `(steam_installation_key,
   steam_account_id32, desktop_id)`. No deviation.
2. Collisions are resolved **during discovery**, before persistent state is
   involved.
3. There is exactly **one effective application per desktop ID**.
4. Normal XDG precedence applies first. Only files colliding at the **same**
   precedence level need tie-breaking.
5. Same-level ties are broken **deterministically**, e.g. stable lexical
   ordering of the absolute path.
6. Discovery emits a **`desktop_id_collision` diagnostic** and **retains every
   colliding source path** for debugging.
7. Colliding physical files never get **separate Steam-AppID state rows**.

#### ~~OPEN-2a — Does a collision block import?~~ (resolved 2026-09-09)

Identity and importability are **separate concerns**:

8. The deterministic winner from point 5 establishes **identity**. Discovery
   always produces exactly one entry for the ID.
9. That entry is marked **unsupported / not importable** until the user
   acknowledges the collision. Importability, not identity, is what the
   collision blocks.

So discovery never refuses to *resolve* a colliding ID, and never emits two
entries for it; it resolves one and withholds import consent.

#### Implemented 2026-09-09

All nine points are in place:

- `_desktop_files` now sorts on the **whole absolute path string** rather than
  relying on `os.walk` order, so the tie-break is a property of the paths and
  not of the filesystem. This reorders scan output: a top-level file no longer
  automatically precedes nested ones (`a.desktop` < `a/a.desktop` < `b.desktop`).
- `_group_by_desktop_id` collects each root's files per ID, which makes a
  same-level collision visible without a second pass.
- `DesktopIdCollision` records the ID, root, every colliding path, and the
  winner. Exposed as `DiscoveryResult.collisions` and printed by `debug scan`.
- `DesktopApplication.collision_paths` / `.has_collision` retain the losing
  paths on the resolved entry.
- `UnsupportedCode.DESKTOP_ID_COLLISION` withholds import consent. It is the
  only *liftable* unsupported code.
- `discover_applications(acknowledged_collisions=...)` lifts the block only
  when the acknowledgement still names this winner and colliding set.

Behaviours worth noting, each covered by a test:

- A collision in a **lower-priority** root is not reported, because precedence
  already settled the ID and the tie is moot.
- An **unparsable** lexical winner still yields to the next candidate; the
  existing "unparsable files do not claim an ID" rule survives collisions.
- An entry that is **already unsupported** keeps its original reason, since
  that is more useful than the collision; `collision_paths` stays populated.
- Acknowledgement is persisted in the Phase 5 store and remains global (a
  host-filesystem property, not a per-Steam-account one). The row stores the
  winning source path, the colliding path set, and a fingerprint of both.
  If a later scan resolves a different winner or a different colliding set,
  the acknowledgement does not apply and import consent is required again.
  Desktop-ID-only rows from the first Phase 5 schema are discarded on open.

Verified against the capture host: the real
`ons-dev.vencord.Vesktop.desktop` collision is detected, blocks import
(importable 779 → 778), and is lifted by acknowledgement.

### ~~OPEN-3 — Flatpak `--file-forwarding` markers survive `%U` removal~~

**Resolved 2026-09-09. Implemented in the Phase 2 Flatpak adapter, and the
open empirical question has now been answered.**

`--file-forwarding` wraps document arguments in `@@u` and `@@`. Dropping `%U`
correctly leaves those markers behind:

```text
flatpak run ... --file-forwarding org.example.App @@u @@
```

The Phase 1 parser is right not to touch non-field-code tokens. **Decision:**
the Phase 2 Flatpak adapter strips the marker block *and* the
`--file-forwarding` flag itself whenever no document is passed, which is
always the case for Steam shortcuts. Confirmed against a real export
(`us.zoom.Zoom`).

#### Empirical result — stripping was *not* strictly necessary

The decision was taken without knowing whether `flatpak` consumes stray
markers itself. It does. Measured with a real Flatpak, using `--command=echo`
so nothing real launches:

| Command | Application receives |
| --- | --- |
| `flatpak run --command=echo --file-forwarding us.zoom.Zoom HELLO @@u @@` | `HELLO` |
| `flatpak run --command=echo us.zoom.Zoom HELLO @@u @@` | `HELLO @@u @@` |

So with `--file-forwarding` present the markers never reach the application,
and leaving them would have been harmless. Stripping is kept anyway: it makes
the stored shortcut self-explanatory and does not depend on undocumented
`flatpak` argument handling staying the way it is. This is now a *preference*,
not a correctness fix, and the checklist says so rather than implying the
importer discovered a bug.

The second row is load-bearing in the other direction: **without** the flag
the markers are ordinary arguments and reach the application, which is why
`_strip_file_forwarding` refuses to touch argv unless `--file-forwarding` is
actually present.

#### Implemented

`_strip_file_forwarding` in `launch/adapters.py` removes `--file-forwarding`
together with every `@@`/`@@u` … `@@` region, but only when all such regions
are empty. A region that still holds arguments, or one that is unterminated,
leaves argv untouched and records a warning; a half-removed forwarding block
would be worse than an untouched one.

Verified on the capture host: 0 of 804 entries retain a marker or the flag,
and no launch vector produced a warning.

### OPEN-4 — Invalid booleans fall back in an unsafe direction

**Severity: low. Deliberately deferred, but must stay open: a conservative
malformed-`Hidden` policy is required before release.**

`Terminal='False'` appears on the capture host (7 entries). Quoted values are
invalid, so they fall back to the default. Here that happens to be correct,
but `Hidden='True'` would fall back to `False` and un-mask an entry the
packager intended to hide. §8 does not discuss invalid values.

Current behaviour is unchanged and matches GLib. That is fine for `Terminal`
and `NoDisplay`, where a wrong fallback is cosmetic, but not for `Hidden`,
which is a **masking** key: getting it wrong resurrects an entry someone
deliberately hid, and the importer would then offer it for import.

The asymmetry to resolve before release: an unparsable `Hidden` value should
probably be treated as masking, or at least as "do not offer for import",
rather than silently defaulting to `False`. Not changed yet because no
malformed `Hidden` has been observed in the wild, so there is no evidence for
which direction real packagers intend. **Release gate: decide this
explicitly; do not let the GLib default stand by omission.**

### OPEN-5 — "missing `TryExec`" is ambiguous in the Phase 1 fixture list

**Severity: low. Resolved by covering both readings.**

§31's Phase 1 fixture list says "missing `TryExec`", which could mean the key
is absent or the binary is absent. Both fixtures exist
(`semantics-tryexec-absent`, `semantics-tryexec-unresolvable`) and behave
differently: absent means availability is unknown and the entry stays
importable; unresolvable marks it unavailable per §8.

### ~~OPEN-6 — `LastPlayTime` for new shortcuts is unspecified~~

**Severity: low. Accepted in Phase 6: `LastPlayTime = 0` for newly created
shortcuts, meaning never played.**

§15 lists the field but gives no value for newly created entries. Real entries
carry both `0` and real timestamps. The Steam-written fixture schema uses `0`,
so new importer entries match that.

---

## Untested / experimental — must not be treated as settled

Carried forward from §32 and §36. None of these were validated:

| Item | Why not |
| --- | --- |
| TEST-001 Flatpak Steam host launching | No Flatpak Steam on the capture host |
| TEST-002 Account-selection hints | Only one Steam account available; multi-account logic is fixture-tested only |
| TEST-003 `FlatpakAppID` | Requires live A/B shortcuts |
| TEST-004 `ShortcutPath` | Requires live A/B shortcuts |
| TEST-005 Artwork hot reload | Requires a running Steam |
| Steam's own AppID algorithm | Explicitly not claimed (rule 9) |
| Binary KeyValues case-insensitivity | Assumed, not verified |

A second test environment with Flatpak Steam and two Steam accounts is
required before any of the first two can move out of "experimental". See
`docs/TEST_ENVIRONMENT.md` "Environment B".

---

## Release gate (§34) — status

| Requirement | Status |
| --- | --- |
| XDG precedence tests pass | **yes** |
| Hidden masking tests pass | **yes** |
| Exec grammar fixtures pass | **yes** |
| env-wrapper tests pass | **yes** |
| persistent identity/AppID tests pass | **yes** — Phase 5 |
| AppID allocation collision tests pass | **yes** — Phase 5 |
| desktop-ID collision tests pass | **yes** — see OPEN-2 |
| VDF round-trip fixtures pass | **yes** — Phase 6; live file also byte-identical |
| atomic-write failure-injection tests pass | **yes** — Phase 7, tmp copies |
| native Steam end-to-end import passes | not started (Phase 10) |
| unrelated shortcuts survive repeated imports | not started (Phase 10) |
| malformed-`Hidden` policy decided (OPEN-4) | **no** — must not ship by omission |
| backups are recoverable | **yes** — Phase 7 timestamped copies; not crash-tested on real hardware |
| unsigned 32-bit artwork naming confirmed | **yes** — 565/565 on real data |
| SteamGridDB failures cannot corrupt Steam state | **yes** — Phase 8 client never writes Steam paths |
