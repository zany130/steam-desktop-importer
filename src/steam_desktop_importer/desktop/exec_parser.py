"""Spec-compliant Desktop Entry ``Exec=`` parsing.

IMPLEMENTATION.md §9: the ``Exec=`` value is **not** a shell command, and
``shlex.split()`` semantics are not the Desktop Entry grammar. Field codes are
handled semantically, never as a blanket regex deletion.

Two processing stages, in the order the Desktop Entry specification requires:

1. **String-value unescaping.** ``Exec`` has value type ``string``, so the
   escape sequences ``\\s``, ``\\n``, ``\\t``, ``\\r`` and ``\\\\`` are resolved
   first. The specification is explicit that this happens *before* quoting,
   which is why representing a literal backslash inside a quoted argument
   takes four backslashes in the file.
2. **Exec quoting.** Arguments are separated by whitespace and may be enclosed
   in double quotes. Inside double quotes, ``"``, ``` ` ```, ``$`` and ``\\``
   are escaped with a backslash.

Field-code expansion is a third, separate stage that operates on *tokens*, not
on the raw string. It has to: ``%i`` expands to two arguments, ``%F`` and
``%U`` expand to a variable number, and ``%c`` may contain spaces yet must
remain exactly one argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "EXEC_RESERVED_CHARACTERS",
    "PARSE_MODE_COMPAT",
    "PARSE_MODE_STRICT",
    "ExecParseError",
    "ExecParseResult",
    "FieldCodeContext",
    "looks_like_shell_single_quoting",
    "parse_exec",
    "tokenize_exec",
    "unescape_entry_value",
    "uses_env_wrapper",
]

PARSE_MODE_STRICT = "strict"
"""The Desktop Entry grammar: ``'`` is a reserved but ordinary character."""

PARSE_MODE_COMPAT = "compat"
"""Strict grammar plus POSIX-shell single-quote handling. Opt-in per entry."""


class ExecParseError(ValueError):
    """The ``Exec=`` value could not be tokenized."""


# Desktop Entry specification, "The Exec key": characters that must be quoted
# if they are to appear literally in an argument.
EXEC_RESERVED_CHARACTERS = frozenset(' \t\n"\'\\><~|&;$*?#()`')

# Value-type escapes for `string` and `localestring`.
_VALUE_ESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}

# Inside a double-quoted argument these may be escaped with a backslash.
_QUOTE_ESCAPABLE = frozenset('"`$\\')

_ARGUMENT_SEPARATORS = " \t\n"

# Field codes that the specification marks deprecated. They expand to nothing.
_DEPRECATED_CODES = frozenset("dDnNvm")

# Field codes that consume documents or URLs.
_FILE_CODES = frozenset("fF")
_URL_CODES = frozenset("uU")


@dataclass(frozen=True)
class FieldCodeContext:
    """Everything field-code expansion is allowed to substitute.

    The importer normally launches a shortcut with no document and no URL, so
    ``files`` and ``urls`` are usually empty and the corresponding codes
    expand to nothing.
    """

    icon: str | None = None
    localized_name: str | None = None
    desktop_file_location: str | None = None
    files: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()


@dataclass
class _Report:
    codes: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecParseResult:
    """Structured result of parsing one ``Exec=`` value."""

    raw: str
    """The value exactly as it appeared in the file."""

    tokens: tuple[str, ...]
    """Tokens after unescaping and quoting, before field-code expansion."""

    argv: tuple[str, ...]
    """Launch-ready argument vector after field-code expansion."""

    field_codes: tuple[str, ...]
    """Every field code encountered, in order, including duplicates."""

    deprecated_field_codes: tuple[str, ...]
    dropped_tokens: tuple[str, ...]
    """Tokens removed entirely because a file/URL code had nothing to expand to."""

    warnings: tuple[str, ...]

    parse_mode: str = PARSE_MODE_STRICT
    """Which tokenizer produced ``tokens``. See :func:`parse_exec`."""

    @property
    def nonstandard(self) -> bool:
        """Whether the value needed non-specification handling to tokenize."""
        return self.parse_mode != PARSE_MODE_STRICT

    @property
    def program(self) -> str | None:
        """First element of ``argv``, if any."""
        return self.argv[0] if self.argv else None

    @property
    def accepts_documents(self) -> bool:
        """Whether the entry declares a file or URL field code."""
        return any(code[1] in _FILE_CODES | _URL_CODES for code in self.field_codes)


def unescape_entry_value(raw: str) -> str:
    """Resolve Desktop Entry ``string`` value escapes.

    Unrecognized escape sequences are undefined by the specification. GLib
    treats them as an error; we keep the backslash literally so that nothing
    is silently dropped, and the caller can still see the original text.
    """
    if "\\" not in raw:
        return raw

    out: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            out.append("\\")
            index += 1
            continue
        replacement = _VALUE_ESCAPES.get(raw[index + 1])
        if replacement is None:
            out.append("\\")
            index += 1
        else:
            out.append(replacement)
            index += 2
    return "".join(out)


def _tokenize(
    value: str, honour_single_quotes: bool = False
) -> tuple[list[str], list[bool], list[str]]:
    """Apply the Exec quoting rule.

    Returns the tokens, a parallel list recording whether each token contained
    any quoted section, and any warnings.

    With ``honour_single_quotes`` the *only* change is that ``'`` opens a
    literal-quoted region, as POSIX shells and GLib treat it. Everything else,
    including the double-quote escape rules, stays exactly as the Desktop
    Entry specification defines it. Keeping the deviation this narrow means a
    compatibility parse differs from a strict parse in one respect only.
    """
    tokens: list[str] = []
    quoted_flags: list[bool] = []
    warnings: list[str] = []

    current: list[str] = []
    started = False
    current_quoted = False
    seen_single_quote = False

    index = 0
    length = len(value)
    while index < length:
        char = value[index]

        if char in _ARGUMENT_SEPARATORS:
            if started:
                tokens.append("".join(current))
                quoted_flags.append(current_quoted)
                current = []
                started = False
                current_quoted = False
            index += 1
            continue

        if char == '"':
            started = True
            current_quoted = True
            index += 1
            closed = False
            while index < length:
                inner = value[index]
                if inner == "\\":
                    if index + 1 < length and value[index + 1] in _QUOTE_ESCAPABLE:
                        current.append(value[index + 1])
                        index += 2
                        continue
                    # A backslash before anything else is undefined inside a
                    # quoted argument. Keep it literal.
                    current.append("\\")
                    index += 1
                    continue
                if inner == '"':
                    closed = True
                    index += 1
                    break
                current.append(inner)
                index += 1
            if not closed:
                raise ExecParseError(f"unterminated double quote in Exec value: {value!r}")
            continue

        if char == "'" and honour_single_quotes:
            started = True
            current_quoted = True
            index += 1
            closed = False
            while index < length:
                if value[index] == "'":
                    closed = True
                    index += 1
                    break
                # Nothing is escapable inside a POSIX single-quoted region.
                current.append(value[index])
                index += 1
            if not closed:
                raise ExecParseError(f"unterminated single quote in Exec value: {value!r}")
            continue

        if char == "'" and not seen_single_quote:
            seen_single_quote = True
            warnings.append(
                "Exec value contains a single quote, which the Desktop Entry "
                "specification reserves; treating it as a literal character"
            )

        started = True
        current.append(char)
        index += 1

    if started:
        tokens.append("".join(current))
        quoted_flags.append(current_quoted)

    return tokens, quoted_flags, warnings


def tokenize_exec(raw: str, honour_single_quotes: bool = False) -> list[str]:
    """Unescape and tokenize an ``Exec=`` value without expanding field codes."""
    tokens, _, _ = _tokenize(unescape_entry_value(raw), honour_single_quotes)
    return tokens


def looks_like_shell_single_quoting(value: str) -> bool:
    """Whether ``value`` uses ``'`` in a recognisable shell-quoting pattern.

    This is the gate on compatibility parsing. It has to separate two very
    different uses of the same character:

    * quoting, as in ``-b 'EMU Stuff'``, which the strict grammar mangles;
    * an apostrophe, as in ``don't``, where the strict grammar is correct and
      a shell parser would be wrong.

    The pattern is only recognised when *every* single quote outside a
    double-quoted region participates in a well-formed pair, each opening
    quote sits at an argument boundary (start of value, whitespace, or after
    ``=``), and each closing quote is followed by whitespace or ends the
    value. ``don't`` fails on the opening-quote position, and ``it's a b's``
    fails for the same reason even though its quotes happen to balance.

    Single quotes appearing *inside* a double-quoted region are literal in
    both grammars and are skipped entirely, so ``bash -c "'/path/x.sh'"``
    is correctly left to the strict parser.

    ``value`` must already have had its string-value escapes resolved.
    """
    positions: list[int] = []
    index = 0
    length = len(value)
    in_double = False

    while index < length:
        char = value[index]
        if in_double:
            if char == "\\" and index + 1 < length and value[index + 1] in _QUOTE_ESCAPABLE:
                index += 2
                continue
            if char == '"':
                in_double = False
            index += 1
            continue
        if char == '"':
            in_double = True
            index += 1
            continue
        if char == "'":
            positions.append(index)
        index += 1

    if in_double or not positions or len(positions) % 2:
        return False

    boundary_before = set(_ARGUMENT_SEPARATORS) | {"="}
    for opening, closing in zip(positions[0::2], positions[1::2]):
        if opening > 0 and value[opening - 1] not in boundary_before:
            return False
        if closing + 1 < length and value[closing + 1] not in _ARGUMENT_SEPARATORS:
            return False
    return True


def _expand_token(
    token: str,
    was_quoted: bool,
    context: FieldCodeContext,
    report: _Report,
) -> list[str] | None:
    """Expand one token. Returns ``None`` if the token should be dropped."""
    if "%" not in token:
        return [token]

    if was_quoted:
        report.warnings.append(
            f"field code used inside a quoted argument {token!r}; the Desktop "
            "Entry specification leaves this undefined"
        )

    # Codes that expand to a number of arguments other than one must be
    # handled at token level, before any character-wise scan.
    if token == "%i":
        report.codes.append("%i")
        if context.icon:
            return ["--icon", context.icon]
        return []

    if token in ("%f", "%F", "%u", "%U"):
        report.codes.append(token)
        values = context.files if token in ("%f", "%F") else context.urls
        # A standalone document/URL code expands to however many values there
        # are, which is normally zero. That is an expansion to nothing, not a
        # dropped token, so it is not reported as dropped.
        return list(values[:1]) if token in ("%f", "%u") else list(values)

    out: list[str] = []
    drop = False
    saw_code = False
    index = 0
    length = len(token)

    while index < length:
        char = token[index]
        if char != "%":
            out.append(char)
            index += 1
            continue

        if index + 1 >= length:
            report.warnings.append(f"trailing '%' in Exec token {token!r}")
            out.append("%")
            index += 1
            continue

        code = token[index + 1]
        index += 2

        # %% is a literal percent and is deliberately not recorded as a field
        # code. Consuming both characters here is what stops a naive scan from
        # finding a phantom %f inside a value such as "%%%%f".
        if code == "%":
            out.append("%")
            continue

        saw_code = True
        report.codes.append("%" + code)

        if code in _DEPRECATED_CODES:
            report.deprecated.append("%" + code)
            report.warnings.append(f"deprecated field code %{code} expands to nothing")
            continue

        if code == "c":
            out.append(context.localized_name or "")
            continue

        if code == "k":
            out.append(context.desktop_file_location or "")
            continue

        if code == "f":
            if context.files:
                out.append(context.files[0])
            else:
                drop = True
            continue

        if code == "u":
            if context.urls:
                out.append(context.urls[0])
            else:
                drop = True
            continue

        if code in ("F", "U"):
            report.warnings.append(
                f"list field code %{code} is embedded in token {token!r}; it can "
                "only expand to multiple arguments when it stands alone"
            )
            values = context.files if code == "F" else context.urls
            if values:
                out.append(values[0])
            else:
                drop = True
            continue

        if code == "i":
            report.warnings.append(
                f"%i is embedded in token {token!r}; it expands to two arguments "
                "and can only be used standalone, so it is being dropped"
            )
            continue

        report.warnings.append(f"unknown field code %{code} in token {token!r}; dropped")

    text = "".join(out)

    if drop:
        if text:
            # Deleting just the code would leave something like "--file=",
            # which is a different command. Drop the whole token instead.
            report.warnings.append(
                f"dropped token {token!r}: it combines a document/URL field code "
                "with other text, and no document or URL is being passed"
            )
        report.dropped.append(token)
        return None

    if not text and saw_code:
        # The token consisted only of codes that expanded to nothing, such as
        # a deprecated code. Emitting "" would add a spurious empty argument.
        return []

    return [text]


def parse_exec(
    raw: str,
    context: FieldCodeContext | None = None,
    allow_compatibility: bool = True,
) -> ExecParseResult:
    """Parse an ``Exec=`` value into a structured argument vector.

    The strict Desktop Entry grammar is always tried first. Compatibility
    parsing is never applied globally: it is only reached when the strict
    result would demonstrably be wrong, which requires all three of

    1. :func:`looks_like_shell_single_quoting` recognising the pattern,
    2. the compatibility tokenizer succeeding, and
    3. its tokens actually differing from the strict tokens.

    When that happens the result carries ``parse_mode == PARSE_MODE_COMPAT``
    and ``nonstandard is True``, so callers can surface it rather than
    silently accepting a non-specification entry.

    Args:
        raw: The ``Exec`` value exactly as read from the desktop file.
        context: Substitutions available to field codes. Defaults to an empty
            context, i.e. no icon, no name, no desktop-file location, and no
            documents or URLs, which is the normal Steam-shortcut case.
        allow_compatibility: Set ``False`` to force strict-only parsing.

    Raises:
        ExecParseError: If the value cannot be tokenized under the strict
            grammar.
    """
    ctx = context or FieldCodeContext()
    unescaped = unescape_entry_value(raw)

    tokens, quoted_flags, tokenize_warnings = _tokenize(unescaped)
    parse_mode = PARSE_MODE_STRICT

    if allow_compatibility and looks_like_shell_single_quoting(unescaped):
        try:
            compat = _tokenize(unescaped, honour_single_quotes=True)
        except ExecParseError:
            compat = None
        if compat is not None and compat[0] != tokens:
            tokens, quoted_flags, tokenize_warnings = compat
            parse_mode = PARSE_MODE_COMPAT
            tokenize_warnings = list(tokenize_warnings)
            tokenize_warnings.append(
                "Exec uses POSIX shell single-quote quoting, which the Desktop "
                "Entry specification reserves; the strict grammar would split "
                "these arguments incorrectly, so a compatibility tokenizer was "
                "used and the entry is marked nonstandard"
            )

    report = _Report(warnings=list(tokenize_warnings))
    argv: list[str] = []
    for token, was_quoted in zip(tokens, quoted_flags):
        expanded = _expand_token(token, was_quoted, ctx, report)
        if expanded is not None:
            argv.extend(expanded)

    document_codes = [code for code in report.codes if code[1] in _FILE_CODES | _URL_CODES]
    if len(document_codes) > 1:
        report.warnings.append(
            "more than one of %f/%F/%u/%U is present; the Desktop Entry "
            f"specification allows only one: {document_codes}"
        )

    return ExecParseResult(
        raw=raw,
        tokens=tuple(tokens),
        argv=tuple(argv),
        field_codes=tuple(report.codes),
        deprecated_field_codes=tuple(report.deprecated),
        dropped_tokens=tuple(report.dropped),
        warnings=tuple(report.warnings),
        parse_mode=parse_mode,
    )


def uses_env_wrapper(argv: tuple[str, ...] | list[str]) -> bool:
    """Whether ``argv`` starts with an ``env``-style environment wrapper.

    IMPLEMENTATION.md §9 and non-negotiable rule 5 forbid rewriting such a
    wrapper into ``LaunchOptions``. This helper exists so that the Phase 2
    launch adapters can *recognise* the pattern in order to preserve it, not
    in order to unwrap it.
    """
    if not argv:
        return False
    first = argv[0]
    program = first.rsplit("/", 1)[-1]
    if program != "env":
        return False
    return any("=" in argument and not argument.startswith("-") for argument in argv[1:])
