"""Exec= grammar tests (IMPLEMENTATION.md §9, Phase 1 fixture list, §34)."""

from __future__ import annotations

import pytest

from steam_desktop_importer.desktop.exec_parser import (
    ExecParseError,
    FieldCodeContext,
    parse_exec,
    tokenize_exec,
    unescape_entry_value,
    uses_env_wrapper,
)
from steam_desktop_importer.desktop.parser import parse_desktop_entry


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
    assert any("single quote" in warning for warning in result.warnings)


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
