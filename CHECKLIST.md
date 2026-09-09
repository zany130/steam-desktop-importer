# Specification Checklist

Tracks IMPLEMENTATION.md compliance. Updated as phases land.

**Current state: Phase 0 and Phase 1 complete. 156 tests passing.**
No code in this repository writes to any Steam directory, and a test enforces
that (`test_package_performs_no_filesystem_writes`).

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

### §33 Debug commands (partial)

- [x] `debug roots`
- [x] `debug scan`
- [x] `debug desktop-entry <path>`
- [ ] `debug dump-shortcuts` — needs Phase 4/6
- [ ] `debug identity` — needs Phase 4/5

Deliberately absent rather than stubbed, so no command can appear to work
while returning guessed data.

## Not started

Phases 2–11, and §11–§30 in general. Specifically **not** implemented, as
instructed:

- Steam collections/categories (§24, rule 20) — out of scope for MVP
- Any Flatpak permission modification (§11, rule 23)
- Any live `shortcuts.vdf` write (rule 15, Phase 7 gate)
- GUI (Phase 3)

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

### DEV-7 — Lenient boolean handling

Only `true`/`false` are valid per the specification. Legacy `1`/`0` are
accepted with a warning, since they were valid in an older revision and still
appear in the wild. Anything else falls back to the key's default with a
warning, matching GLib. See OPEN-4 for why the fallback direction is not
always safe.

---

## Open issues

These need a decision. They are **not** resolved unilaterally; current
behaviour is pinned by `tests/unit/test_open_issues.py` so any change is
deliberate.

### OPEN-1 — Single quotes: specification compliance breaks real entries

**Severity: high. Blocks Phase 2.**

The Desktop Entry specification reserves `'` and says it must not be used, so
a compliant tokenizer treats it as an ordinary literal character. Real
generated launchers use it as POSIX shell quoting anyway.

**33 of 804 entries on the capture host are affected.** For example, Bottles
writes:

```text
Exec=flatpak run --command=bottles-cli com.usebottles.bottles run -p EzRO -b 'EMU Stuff' -- %u
```

Strict parsing yields `... '-b', "'EMU", "Stuff'", '--']` — two broken
arguments instead of one. GLib's `g_shell_parse_argv`, which real desktop
environments use to launch these, honours the quoting and produces
`EMU Stuff`.

So "spec-compliant" and "launches the same way the desktop does" genuinely
conflict here. Current behaviour is strict-plus-warning, because Phase 1
imports nothing and the decision can be made where it matters.

Options for Phase 2:

1. Stay strict, and mark affected entries unsupported so §8's "do not silently
   import a broken shortcut" is honoured.
2. Match GLib and honour single quotes as shell-style quoting.
3. Stay strict by default with an opt-in GLib-compatible mode.

### OPEN-2 — Desktop IDs are not unique

**Severity: medium. Affects §6 persistent state.**

The FreeDesktop scheme replaces `/` with `-` and does not escape existing
dashes, so `vendor/app.desktop` and `vendor-app.desktop` in the same root
produce the same ID. Observed for real on the capture host
(`~/.local/share/applications/ons/dev.vencord.Vesktop.desktop` versus
`~/.local/share/applications/ons-dev.vencord.Vesktop.desktop`).

§7.4 resolves the display side deterministically. The problem is §6, which
keys persistent Desktop-ID → AppID state on the desktop ID alone: two
different applications would share one state row, and which one owns it could
change if a file is added or removed.

Needs a decision before Phase 5. Adding the source root to the state key would
change §6's stated logical identity, so it is not being done unilaterally.

### OPEN-3 — Flatpak `--file-forwarding` markers survive `%U` removal

**Severity: medium. Phase 2 work, flagged now.**

`--file-forwarding` wraps document arguments in `@@u` and `@@`. Dropping `%U`
correctly leaves those markers behind:

```text
flatpak run ... --file-forwarding org.example.App @@u @@
```

The Phase 1 parser is right not to touch non-field-code tokens. The Phase 2
Flatpak adapter has to strip the marker pair when no document is passed.
Confirmed against a real export (`us.zoom.Zoom`).

### OPEN-4 — Invalid booleans fall back in an unsafe direction

**Severity: low.**

`Terminal='False'` appears on the capture host (7 entries). Quoted values are
invalid, so they fall back to the default. Here that happens to be correct,
but `Hidden='True'` would fall back to `False` and un-mask an entry the
packager intended to hide. §8 does not discuss invalid values.

### OPEN-5 — "missing `TryExec`" is ambiguous in the Phase 1 fixture list

**Severity: low. Resolved by covering both readings.**

§31's Phase 1 fixture list says "missing `TryExec`", which could mean the key
is absent or the binary is absent. Both fixtures exist
(`semantics-tryexec-absent`, `semantics-tryexec-unresolvable`) and behave
differently: absent means availability is unknown and the entry stays
importable; unresolvable marks it unavailable per §8.

### OPEN-6 — `LastPlayTime` for new shortcuts is unspecified

**Severity: low. Phase 6.**

§15 lists the field but gives no value for newly created entries. Real entries
carry both `0` and real timestamps.

---

## Untested / experimental — must not be treated as settled

Carried forward from §32 and §36. None of these were validated:

| Item | Why not |
| --- | --- |
| TEST-001 Flatpak Steam host launching | No Flatpak Steam on the capture host |
| TEST-002 Account-selection hints | Only one Steam account available |
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
| Exec grammar fixtures pass | **yes**, with OPEN-1 outstanding |
| env-wrapper tests pass | **yes** |
| persistent identity/AppID tests pass | not started (Phase 5) |
| collision tests pass | not started (Phase 5) |
| VDF round-trip fixtures pass | fixtures exist; round-trip is Phase 6 |
| atomic-write failure-injection tests pass | not started (Phase 7) |
| native Steam end-to-end import passes | not started (Phase 10) |
| unrelated shortcuts survive repeated imports | not started (Phase 10) |
| backups are recoverable | not started (Phase 7) |
| unsigned 32-bit artwork naming confirmed | **yes** — 565/565 on real data |
| SteamGridDB failures cannot corrupt Steam state | not started (Phase 8) |
