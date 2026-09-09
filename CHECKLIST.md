# Specification Checklist

Tracks IMPLEMENTATION.md compliance. Updated as phases land.

**Current state: Phase 0 and Phase 1 complete. 177 tests passing.**
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
- [x] Strict grammar first; per-entry compatibility retry only for recognised
      non-standard single-quote patterns, marked `nonstandard_exec` /
      `exec_parse_mode="compat"` (DEV-8)

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
| `'\''` escape idiom | `-c '/bin/svc -o '\''%u'\'''` | 1 entry → **strict**, needs a real shell parser; reported via warnings |
| Apostrophes | `don't`, `it's a b's` | → **strict**, a shell parser would corrupt these |

Note the fourth row: `it's a b's` has *balanced* quotes, so a naive balance
check would silently turn it into `its a bs`. The argument-boundary rule is
what prevents that.

Verified on the capture host: 0 entries flipped to compat without their argv
actually changing, and parse errors stayed at 0.

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

| Issue | Severity | Status |
| --- | --- | --- |
| OPEN-1 single quotes | high | **resolved** → DEV-8 |
| OPEN-2 desktop ID collisions | medium | **decided**, except OPEN-2a |
| OPEN-2a does a collision block import? | medium | **open** — blocks implementation |
| OPEN-3 Flatpak file-forwarding markers | medium | **open** — Phase 2 |
| OPEN-4 invalid booleans | low | default proposed |
| OPEN-5 "missing TryExec" ambiguity | low | **resolved** — both readings covered |
| OPEN-6 `LastPlayTime` for new shortcuts | low | default proposed |

### ~~OPEN-1 — Single quotes~~

**Resolved. See DEV-8.**

### OPEN-2 — Desktop IDs are not unique

**Severity: medium. Affects §6 persistent state. Decided 2026-09-09, with one
point still to confirm — see OPEN-2a.**

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

#### Settled

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

#### OPEN-2a — Does a collision block import?

The two halves of the decision disagree here and this is **not** being
resolved unilaterally:

- "Detect collisions at discovery time and refuse to import either colliding
  entry until the user resolves it" implies the colliding entries are
  **unsupported** until acknowledged.
- "Choose one deterministically ... emit a `desktop_id_collision` diagnostic"
  implies discovery **picks a winner and proceeds**.

They reconcile if the deterministic winner establishes *identity* while a
separate policy governs *importability*: pick the winner, emit the diagnostic,
and mark that one entry unsupported until acknowledged. That reading has not
been confirmed, so no collision handling has been implemented yet.

Current behaviour is unchanged: §7.4 first-match-wins, loser recorded in
`DiscoveryResult.shadowed`, no diagnostic, no import block. Pinned by
`test_open_2_desktop_ids_can_collide_between_nested_and_dashed_paths`.

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

**Severity: low. Proposed default accepted unless objected to: keep current
behaviour, which matches GLib exactly.**

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

**Severity: low. Phase 6. Proposed default accepted unless objected to:
`LastPlayTime = 0` for newly created shortcuts, meaning never played.**

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
| Exec grammar fixtures pass | **yes** |
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
