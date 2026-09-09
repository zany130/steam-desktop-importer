# Desktop entry fixtures

Built for IMPLEMENTATION.md §31 Phase 0. Every case the Phase 1 fixture list
requires is covered; the mapping is at the bottom.

All fixtures are synthetic except where a comment says a case was taken from a
real entry observed on the capture host.

Each fixture carries `Comment=` lines explaining what it is guarding against,
so the intent survives even if a test is later rewritten.

## `xdg_precedence/`

Three `applications/` roots standing in for `$XDG_DATA_HOME`,
`/usr/local/share` and `/usr/share`, in that precedence order.

| Desktop ID | Resolves from | Guards |
| --- | --- | --- |
| `org.example.Overridden.desktop` | `data_home` | §7.4 first-match-wins across all three roots |
| `org.example.MaskedByUser.desktop` | *masked* | rule 3: `Hidden=true` in `data_home` masks two lower copies |
| `org.example.MaskedByLocal.desktop` | *masked* | masking works from a middle-priority root too |
| `nested-vendor-Deep.desktop` | `data_home` | §7.3 nested IDs, and that nesting does not break precedence |
| `org.example.ShadowedByLink.desktop` | `data_home` (`Type=Link`) | a non-Application entry still claims its ID; the lower `Type=Application` copy must stay shadowed |
| `org.example.UserOnly.desktop` | `data_home` | root-unique entries |
| `org.example.LocalOnly.desktop` | `data_dir_local` | root-unique entries |
| `org.example.SystemOnly.desktop` | `data_dir_system` | root-unique entries |

Expected totals: 6 resolved, 2 masked, 7 shadowed, 5 importable.

## `exec_grammar/applications/`

| Fixture | Guards |
| --- | --- |
| `exec-percent-f` / `-F` / `-u` / `-U` | document and URL codes expand to nothing when no document is passed |
| `exec-percent-literal` | `%%` is a literal percent; `%%%%f` must not yield a phantom `%f` |
| `exec-percent-c` | translated name, containing a space, stays exactly one argument |
| `exec-percent-k` | desktop-file location |
| `exec-percent-i` | expands to **two** arguments, `--icon` plus the value |
| `exec-percent-i-no-icon` | expands to **zero** arguments, not a bare `--icon` |
| `exec-quoting` | quoted arguments with spaces and reserved characters |
| `exec-escaped-quote` | a literal `"` inside a quoted argument |
| `exec-literal-backslash` | the specification's four-backslash rule, and `\\$` for a literal `$` |
| `exec-escaped-space` | value escapes really are applied **before** quoting |
| `exec-env-wrapper` | rule 5: the `env` wrapper is preserved verbatim in argv |
| `exec-embedded-fieldcode` | `--file=%f` drops the whole token rather than leaving `--file=` |
| `exec-deprecated-codes` | `%d %n %v` expand to nothing but are reported |
| `exec-with-path` / `exec-no-path` | rule 6: `StartDir` comes from `Path=` or stays empty |
| `exec-bare-command` | a bare command name is left unresolved by the parser |
| `exec-missing` / `exec-empty` | no importable command |
| `exec-single-quote-shell-style` | **real-world case.** Pins current behaviour for CHECKLIST.md OPEN-1 |

## `entry_semantics/applications/`

Covers `Type`, `Hidden`, `NoDisplay`, `Terminal`, `DBusActivatable` with and
without an `Exec` fallback, all four `TryExec` states (absolute-present,
bare-present, unresolvable, absent), `OnlyShowIn`/`NotShowIn`, localization
with country and modifier variants, whitespace around `=`, Desktop Action
groups that must not leak into the main group, a file with no
`[Desktop Entry]` group, a file with no `Name`, and invalid boolean values.

## `sources/`

Provider export trees for source-kind detection.

`flatpak_user_exports/` includes a `--file-forwarding` export whose **last
token is not the app ID**, a export with no `X-Flatpak` key so the ID must be
recovered from argv, and one using a full `app/ID/arch/branch` ref.

`snapd_desktop/` includes a snapd export behind an `env` wrapper, so detection
has to look past the wrapper without rewriting it.

`native_data/` holds an integrated AppImage and a plain native application.

## `id_collision/`

`vendor-app.desktop` and `vendor/app.desktop` in one root derive to the same
desktop ID. Observed for real on the capture host. See CHECKLIST.md OPEN-2.

---

## Phase 1 required-fixture coverage

IMPLEMENTATION.md §31 Phase 1 lists eleven required fixture cases:

| Required | Provided by |
| --- | --- |
| user/system override | `xdg_precedence/` `org.example.Overridden.desktop` |
| Hidden mask | `xdg_precedence/` `org.example.MaskedByUser.desktop`, `MaskedByLocal` |
| nested desktop ID | `xdg_precedence/.../nested/vendor/Deep.desktop` |
| `%u`, `%U`, `%f`, `%F` | `exec-percent-u/-U/-f/-F` |
| `%%` | `exec-percent-literal` |
| `%c` | `exec-percent-c` |
| `%k` | `exec-percent-k` |
| quoting/escaping | `exec-quoting`, `exec-escaped-quote`, `exec-literal-backslash`, `exec-escaped-space` |
| `env VAR=value` | `exec-env-wrapper`, plus `snapd_desktop/example-snap_*` |
| `Path=` | `exec-with-path`, with `exec-no-path` as the negative case |
| missing `TryExec` | `semantics-tryexec-absent` (key absent) and `semantics-tryexec-unresolvable` (binary absent) — the phrase is ambiguous, so both readings are covered |
