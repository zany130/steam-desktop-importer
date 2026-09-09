"""Exec= grammar tests (IMPLEMENTATION.md §9, Phase 1 fixture list, §34)."""

from __future__ import annotations

import pytest

from steam_desktop_importer.desktop.exec_parser import (
    PARSE_MODE_COMPAT,
    PARSE_MODE_STRICT,
    ExecParseError,
    FieldCodeContext,
    looks_like_shell_single_quoting,
    parse_exec,
    tokenize_exec,
    unescape_entry_value,
    uses_env_wrapper,
    uses_shell_quote_escaping,
)
from steam_desktop_importer.desktop.parser import build_application, parse_desktop_entry


def argv_of(directory, filename, locale=None):
    parsed = parse_desktop_entry(directory / filename, locale=locale)
    assert parsed.exec_result is not None
    return parsed.exec_result


# ----------------------------------------------------------------------
# Stage 1: string-value escapes
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"no escapes here", "no escapes here"),
        (r"a\sb", "a b"),
        (r"a\nb", "a\nb"),
        (r"a\tb", "a\tb"),
        (r"a\rb", "a\rb"),
        (r"a\\b", "a\\b"),
        (r"\\\\", "\\\\"),
        # Unknown escape: kept literally rather than silently dropped.
        (r"a\qb", "a\\qb"),
        # Lone trailing backslash.
        ("a\\", "a\\"),
    ],
)
def test_unescape_entry_value(raw, expected):
    assert unescape_entry_value(raw) == expected


# ----------------------------------------------------------------------
# Stage 2: quoting
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/usr/bin/app", ["/usr/bin/app"]),
        ("/usr/bin/app -a -b", ["/usr/bin/app", "-a", "-b"]),
        ("  /usr/bin/app \t -a  ", ["/usr/bin/app", "-a"]),
        ('"/opt/My App/bin" x', ["/opt/My App/bin", "x"]),
        # A quoted section may be glued to unquoted text.
        ('pre"quoted part"post', ["prequoted partpost"]),
        # An explicitly empty argument survives.
        ('/usr/bin/app "" tail', ["/usr/bin/app", "", "tail"]),
    ],
)
def test_tokenize_exec(raw, expected):
    assert tokenize_exec(raw) == expected


def test_unterminated_quote_is_an_error():
    with pytest.raises(ExecParseError, match="unterminated double quote"):
        parse_exec('/usr/bin/app "oops')


def test_single_quote_is_literal_and_warned():
    result = parse_exec("/usr/bin/app it's")
    assert result.argv == ("/usr/bin/app", "it's")
    assert result.parse_mode == PARSE_MODE_STRICT
    assert any("single quote" in warning for warning in result.warnings)


# ----------------------------------------------------------------------
# Strict-first parsing with per-entry compatibility fallback
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "recognised"),
    [
        # Recognised: every quote pairs up at an argument boundary.
        ("/usr/bin/app -b 'EMU Stuff'", True),
        ("/usr/bin/app 'a b' 'c d'", True),
        ("/usr/bin/app --opt='a b'", True),
        ("'/opt/My App/run' --flag", True),
        ("sh -c 'do a thing' sh", True),
        # Not recognised: apostrophes, not quoting.
        ("/usr/bin/app don't", False),
        ("/usr/bin/app it's a b's", False),
        ("/usr/bin/app o'brien o'neill", False),
        # Not recognised: unbalanced.
        ("/usr/bin/app 'a b", False),
        # Not recognised: quotes live inside a double-quoted region.
        ("""bash -c "'/path/to/x.sh'" """, False),
        ('/usr/bin/app "it\'s fine"', False),
        # Not recognised: no single quotes at all.
        ("/usr/bin/app --flag", False),
    ],
)
def test_shell_quoting_pattern_detection(value, recognised):
    assert looks_like_shell_single_quoting(value) is recognised


def test_compat_retry_fixes_a_real_bottles_launcher(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-single-quote-shell-style.desktop")
    assert result.parse_mode == PARSE_MODE_COMPAT
    assert result.nonstandard is True
    assert result.argv == (
        "flatpak",
        "run",
        "--command=bottles-cli",
        "com.usebottles.bottles",
        "run",
        "-p",
        "EzRO",
        "-b",
        "EMU Stuff",
        "--",
    )
    assert any("marked nonstandard" in warning for warning in result.warnings)


def test_compat_retry_keeps_a_shell_script_as_one_argument(exec_grammar_dir):
    """Double quotes inside the single-quoted region stay literal."""
    result = argv_of(exec_grammar_dir, "exec-single-quote-shell-script.desktop")
    assert result.parse_mode == PARSE_MODE_COMPAT
    assert result.argv == (
        "sh",
        "-c",
        'XAPP_FORCE_GTKWINDOW_ICON="/home/example/logo.png" /usr/bin/browser '
        '--class WebApp --no-remote "https://example.invalid"',
    )


def test_apostrophes_stay_strict_even_when_they_balance(exec_grammar_dir):
    """A shell parser would silently turn "it's a b's" into "its a bs"."""
    result = argv_of(exec_grammar_dir, "exec-single-quote-apostrophe.desktop")
    assert result.parse_mode == PARSE_MODE_STRICT
    assert result.nonstandard is False
    assert result.argv == ("/usr/bin/apostrophe", "--msg", "it's", "a", "b's")


def test_single_quotes_inside_double_quotes_stay_strict(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-single-quote-inside-double.desktop")
    assert result.parse_mode == PARSE_MODE_STRICT
    assert result.nonstandard is False
    assert result.argv == (
        "bash",
        "-c",
        "'/var/home/example/.local/share/winezgui/Prefixes/AltInstaller/AltServer.sh'",
    )


def test_posix_quote_escape_idiom_is_not_recognised(exec_grammar_dir):
    r"""'...'\''...' needs a real shell parser, so it stays strict and warns."""
    result = argv_of(exec_grammar_dir, "exec-single-quote-escape-idiom.desktop")
    assert result.parse_mode == PARSE_MODE_STRICT
    assert result.nonstandard is False
    # The mangling is reported rather than passed off as a clean parse.
    assert any("single quote" in warning for warning in result.warnings)
    assert any("dropped token" in warning for warning in result.warnings)

    # ... and, because the argv is known to be wrong rather than merely
    # unusual, the value is flagged so support determination can refuse it.
    # A warning alone would still leave a broken command importable.
    assert result.ambiguous_quoting is True


def test_quote_escape_idiom_produces_a_demonstrably_wrong_argv(exec_grammar_dir):
    r"""Pin *why* this construct is refused rather than parsed.

    A shell reads ``-c '/usr/bin/service -o '\''%u'\'''`` as one argument,
    ``/usr/bin/service -o '%u'``. Both tokenizers here disagree with that, in
    different ways, which is the evidence behind DEV-9.
    """
    result = argv_of(exec_grammar_dir, "exec-single-quote-escape-idiom.desktop")

    # Strict splits on the spaces inside the intended quoting and loses the
    # tail entirely, leaving a stray leading quote on the path.
    assert result.argv[-2:] == ("'/usr/bin/service", "-o")

    # Compat is wrong differently: it emits literal backslashes where the
    # quotes belong, because POSIX unquoted-backslash escaping is not
    # implemented. Neither output may be launched.
    compat = tokenize_exec(result.raw, honour_single_quotes=True)
    assert compat[-1] == "/usr/bin/service -o \\%u\\"


def test_quote_escape_idiom_makes_the_entry_unsupported(exec_grammar_dir):
    """DEV-9: a known-wrong argv must not be importable in Phase 2."""
    path = exec_grammar_dir / "exec-single-quote-escape-idiom.desktop"
    app = build_application(parse_desktop_entry(path), desktop_id=path.name)

    assert app.supported_for_import is False
    assert app.unsupported_code == "exec_ambiguous_quoting"
    # "Unsupported", not "Unavailable": nothing about the system needs fixing.
    assert app.is_available is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (r"sh -c 'a '\''b'\'''", True),
        (r"sh -c 'plain quoting'", False),
        (r"echo don't", False),
        # Inside double quotes a backslash-quote is ordinary Desktop Entry
        # text, not shell escaping, so it must not trip the detector.
        (r'sh -c "it\'s fine"', False),
        (r"/usr/bin/app", False),
    ],
)
def test_shell_quote_escaping_detection_is_narrow(value, expected):
    """The detector must catch the idiom without flagging ordinary values."""
    assert uses_shell_quote_escaping(unescape_entry_value(value)) is expected


def test_nothing_is_escapable_inside_a_single_quoted_region():
    result = parse_exec(r"/usr/bin/app 'a\\b \\$x'")
    assert result.parse_mode == PARSE_MODE_COMPAT
    assert result.argv == ("/usr/bin/app", "a\\b \\$x")


def test_compatibility_can_be_disabled():
    value = "/usr/bin/app -b 'EMU Stuff'"
    assert parse_exec(value).parse_mode == PARSE_MODE_COMPAT
    strict = parse_exec(value, allow_compatibility=False)
    assert strict.parse_mode == PARSE_MODE_STRICT
    assert strict.argv == ("/usr/bin/app", "-b", "'EMU", "Stuff'")


def test_strict_is_the_default_for_ordinary_values(exec_grammar_dir):
    """Compatibility parsing must not leak into entries that do not need it."""
    for filename in (
        "exec-quoting.desktop",
        "exec-env-wrapper.desktop",
        "exec-literal-backslash.desktop",
        "exec-percent-U.desktop",
    ):
        result = argv_of(exec_grammar_dir, filename)
        assert result.parse_mode == PARSE_MODE_STRICT, filename
        assert result.nonstandard is False, filename


def test_double_quote_grammar_is_unchanged_in_compat_mode():
    """Only single-quote handling differs between the two tokenizers."""
    value = r"""/usr/bin/app "C:\\path" 'a b'"""
    assert tokenize_exec(value) == ["/usr/bin/app", "C:\\path", "'a", "b'"]
    assert tokenize_exec(value, honour_single_quotes=True) == [
        "/usr/bin/app",
        "C:\\path",
        "a b",
    ]


# ----------------------------------------------------------------------
# Fixture-driven grammar cases
# ----------------------------------------------------------------------


def test_percent_uppercase_u_is_dropped(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-U.desktop")
    assert result.argv == ("/usr/bin/multi-url", "--new-window")
    assert result.field_codes == ("%U",)
    assert result.accepts_documents


def test_percent_lowercase_u_is_dropped(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-u.desktop")
    assert result.argv == ("/usr/bin/single-url",)


def test_percent_lowercase_f_is_dropped(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-f.desktop")
    assert result.argv == ("/usr/bin/single-file",)


def test_percent_uppercase_f_is_dropped(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-F.desktop")
    assert result.argv == ("/usr/bin/multi-file", "--batch")


def test_document_codes_expand_when_documents_are_supplied():
    context = FieldCodeContext(files=("/tmp/a.txt", "/tmp/b.txt"))
    assert parse_exec("/usr/bin/app %F", context).argv == (
        "/usr/bin/app",
        "/tmp/a.txt",
        "/tmp/b.txt",
    )
    assert parse_exec("/usr/bin/app %f", context).argv == ("/usr/bin/app", "/tmp/a.txt")


def test_double_percent_is_a_literal_percent_and_hides_no_field_code(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-literal.desktop")
    assert result.argv == ("/usr/bin/progress", "--label", "100%", "--pattern", "%%f")
    # Critically, no %f was detected inside "%%%%f".
    assert result.field_codes == ()
    assert not result.accepts_documents


def test_percent_c_uses_the_unlocalized_name_by_default(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-c.desktop")
    assert result.argv == ("/usr/bin/titled", "--title", "Titled Application")


def test_percent_c_uses_the_localized_name_and_stays_one_argument(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-c.desktop", locale="de_DE.UTF-8")
    assert result.argv == (
        "/usr/bin/titled",
        "--title",
        "Betitelte Anwendung (Deutschland)",
    )
    assert len(result.argv) == 3


def test_percent_k_is_the_desktop_file_location(exec_grammar_dir):
    path = exec_grammar_dir / "exec-percent-k.desktop"
    result = argv_of(exec_grammar_dir, "exec-percent-k.desktop")
    assert result.argv == ("/usr/bin/locate-me", "--from", str(path))


def test_percent_i_expands_to_two_arguments(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-i.desktop")
    assert result.argv == ("/usr/bin/iconic", "--icon", "percent-i-icon-name", "--flag")


def test_percent_i_expands_to_nothing_without_an_icon(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-percent-i-no-icon.desktop")
    assert result.argv == ("/usr/bin/iconic", "--flag")


def test_quoting(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-quoting.desktop")
    assert result.argv == (
        "/opt/My Apps/bin/launcher",
        "--title",
        "Hello World",
        "--sep",
        ";",
        "plain",
    )


def test_escaped_quote_inside_a_quoted_argument(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-escaped-quote.desktop")
    assert result.argv == ("/usr/bin/quoter", "--msg", 'say "hi"', "--end")


def test_four_backslashes_are_one_literal_backslash(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-literal-backslash.desktop")
    assert result.argv == (
        "/usr/bin/backslash",
        "C:\\Program Files\\App",
        "--var",
        "$HOME",
    )


def test_value_escapes_are_applied_before_quoting(exec_grammar_dir):
    """The specification's ordering makes an unquoted \\s a real separator."""
    result = argv_of(exec_grammar_dir, "exec-escaped-space.desktop")
    assert result.argv == ("/usr/bin/spacer", "one", "two", "three four")


def test_env_wrapper_is_preserved_verbatim(exec_grammar_dir):
    """IMPLEMENTATION.md rule 5."""
    result = argv_of(exec_grammar_dir, "exec-env-wrapper.desktop")
    assert result.argv == (
        "env",
        "LANG=C",
        "GDK_BACKEND=x11",
        "/usr/bin/real-app",
        "--flag",
    )
    assert uses_env_wrapper(result.argv)
    # The assignments must not have been hoisted out of argv.
    assert "LANG=C" in result.argv
    assert result.argv[0] == "env"


def test_embedded_field_code_drops_the_whole_token(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-embedded-fieldcode.desktop")
    assert result.argv == ("/usr/bin/embedded", "--keep-me")
    assert result.dropped_tokens == ("--file=%f",)
    assert any("dropped token" in warning for warning in result.warnings)


def test_deprecated_codes_expand_to_nothing_but_are_reported(exec_grammar_dir):
    result = argv_of(exec_grammar_dir, "exec-deprecated-codes.desktop")
    assert result.argv == ("/usr/bin/legacy", "--keep")
    assert result.deprecated_field_codes == ("%d", "%n", "%v")
    assert "" not in result.argv


def test_bare_command_is_not_resolved_during_parsing(exec_grammar_dir):
    """Resolution against PATH is a Phase 2 adapter concern, not a parser one."""
    result = argv_of(exec_grammar_dir, "exec-bare-command.desktop")
    assert result.argv == ("sh", "-c", "true")


def test_unknown_field_code_is_dropped_with_a_warning():
    result = parse_exec("/usr/bin/app --opt %z --keep")
    assert result.argv == ("/usr/bin/app", "--opt", "--keep")
    assert any("unknown field code %z" in warning for warning in result.warnings)


def test_multiple_document_codes_are_reported():
    result = parse_exec("/usr/bin/app %f %u")
    assert any("only one" in warning for warning in result.warnings)


def test_field_code_in_a_quoted_argument_is_reported():
    result = parse_exec('/usr/bin/app "%c"', FieldCodeContext(localized_name="Name"))
    assert any("quoted argument" in warning for warning in result.warnings)


def test_uses_env_wrapper_rejects_non_wrappers():
    assert not uses_env_wrapper([])
    assert not uses_env_wrapper(["/usr/bin/app", "FOO=bar"])
    # `env` with no assignments is just a program invocation.
    assert not uses_env_wrapper(["env", "/usr/bin/app"])
    assert uses_env_wrapper(["/usr/bin/env", "FOO=bar", "/usr/bin/app"])
