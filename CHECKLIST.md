# Specification Checklist

Tracks IMPLEMENTATION.md compliance. Updated as phases land.

**Current state: Phases 0, 1, 2 and 4 complete. 241 tests passing.**
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
- [x] `debug launch [desktop-id]` — Phase 2 vectors; never runs or writes
- [x] `debug steam` — Phase 4 installations and accounts; read-only
- [ ] `debug dump-shortcuts` — needs Phase 6
- [ ] `debug identity` — needs Phase 5

Deliberately absent rather than stubbed, so no command can appear to work
while returning guessed data.

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
the chosen installation to be remembered. Both selectors accept a remembered
value and honour it, but **storing** it is Phase 5 (SQLite). Nothing here
writes.

`§14 Steam running detection` is *not* implemented. It gates writing, so it
belongs with Phase 7 rather than here.

## Not started

Phases 3, 5–11, and §14–§30 in general. Specifically **not** implemented, as
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
| OPEN-6 `LastPlayTime` for new shortcuts | low | default proposed |
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
- `discover_applications(acknowledged_collisions=...)` lifts the block while
  keeping the diagnostic.

Behaviours worth noting, each covered by a test:

- A collision in a **lower-priority** root is not reported, because precedence
  already settled the ID and the tie is moot.
- An **unparsable** lexical winner still yields to the next candidate; the
  existing "unparsable files do not claim an ID" rule survives collisions.
- An entry that is **already unsupported** keeps its original reason, since
  that is more useful than the collision; `collision_paths` stays populated.
- Acknowledgement is currently a per-call argument. Persisting it belongs with
  §6 state in Phase 5.

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
| persistent identity/AppID tests pass | not started (Phase 5) |
| AppID allocation collision tests pass | not started (Phase 5) |
| desktop-ID collision tests pass | **yes** — see OPEN-2 |
| VDF round-trip fixtures pass | fixtures exist; round-trip is Phase 6 |
| atomic-write failure-injection tests pass | not started (Phase 7) |
| native Steam end-to-end import passes | not started (Phase 10) |
| unrelated shortcuts survive repeated imports | not started (Phase 10) |
| malformed-`Hidden` policy decided (OPEN-4) | **no** — must not ship by omission |
| backups are recoverable | not started (Phase 7) |
| unsigned 32-bit artwork naming confirmed | **yes** — 565/565 on real data |
| SteamGridDB failures cannot corrupt Steam state | not started (Phase 8) |
