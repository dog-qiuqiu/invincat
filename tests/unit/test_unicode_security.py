"""Tests for Unicode and URL safety helpers."""

from __future__ import annotations

import pytest

from invincat_cli.unicode_security import (
    UnicodeIssue,
    _char_script,
    _decode_hostname,
    _is_local_or_ip_hostname,
    _scripts_in_label,
    _split_hostname_labels,
    check_url_safety,
    detect_dangerous_unicode,
    format_warning_detail,
    iter_string_values,
    looks_like_url_key,
    render_with_unicode_markers,
    strip_dangerous_unicode,
    summarize_issues,
)


def test_unicode_issue_validates_character_and_codepoint() -> None:
    issue = UnicodeIssue(
        position=0,
        character="\u202e",
        codepoint="U+202E",
        name="RIGHT-TO-LEFT OVERRIDE",
    )

    assert issue.codepoint == "U+202E"
    with pytest.raises(ValueError, match="single code point"):
        UnicodeIssue(position=0, character="ab", codepoint="U+0061", name="bad")
    with pytest.raises(ValueError, match="does not match"):
        UnicodeIssue(position=0, character="a", codepoint="U+0062", name="bad")


def test_detect_strip_and_render_dangerous_unicode() -> None:
    text = "safe\u202ehidden\u200b"

    issues = detect_dangerous_unicode(text)

    assert [issue.codepoint for issue in issues] == ["U+202E", "U+200B"]
    assert strip_dangerous_unicode(text) == "safehidden"
    assert (
        render_with_unicode_markers(text)
        == "safe<U+202E RIGHT-TO-LEFT OVERRIDE>hidden<U+200B ZERO WIDTH SPACE>"
    )


def test_summarize_issues_deduplicates_and_truncates() -> None:
    issues = [
        UnicodeIssue(0, "\u202e", "U+202E", "RIGHT-TO-LEFT OVERRIDE"),
        UnicodeIssue(1, "\u202e", "U+202E", "RIGHT-TO-LEFT OVERRIDE"),
        UnicodeIssue(2, "\u200b", "U+200B", "ZERO WIDTH SPACE"),
        UnicodeIssue(3, "\u200c", "U+200C", "ZERO WIDTH NON-JOINER"),
    ]

    assert summarize_issues(issues, max_items=2) == (
        "U+202E RIGHT-TO-LEFT OVERRIDE, U+200B ZERO WIDTH SPACE, +1 more entry"
    )


def test_format_warning_detail_truncates_extra_warnings() -> None:
    assert format_warning_detail(("one", "two", "three"), max_shown=2) == (
        "one; two; +1 more"
    )
    assert format_warning_detail(()) == ""


def test_check_url_safety_accepts_plain_and_local_urls() -> None:
    assert check_url_safety("https://example.com").safe
    assert check_url_safety("https://localhost:8000").safe
    assert check_url_safety("http://127.0.0.1").safe
    assert check_url_safety("not a url").safe


def test_check_url_safety_flags_hidden_unicode_and_mixed_script_domain() -> None:
    hidden = check_url_safety("https://example.com/\u202epath")
    spoofed = check_url_safety("https://раypal.com")

    assert not hidden.safe
    assert "hidden Unicode" in hidden.warnings[0]
    assert not spoofed.safe
    assert any("mixes scripts" in warning for warning in spoofed.warnings)
    assert any("confusable" in warning for warning in spoofed.warnings)


def test_check_url_safety_decodes_punycode_and_flags_spoofed_label() -> None:
    result = check_url_safety("https://xn--pple-43d.com")

    assert result.decoded_domain == "аpple.com"
    assert not result.safe
    assert any("Punycode domain decodes" in warning for warning in result.warnings)


def test_check_url_safety_flags_invalid_punycode_label() -> None:
    result = check_url_safety("https://xn--.example.com")

    assert not result.safe
    assert result.decoded_domain is None
    assert any("could not be decoded" in warning for warning in result.warnings)


def test_hostname_helpers_and_script_classification() -> None:
    assert _decode_hostname("example.com") == ("example.com", [])
    assert _split_hostname_labels(".example..com.") == ["example", "com"]
    assert not _is_local_or_ip_hostname("   .")
    assert not _is_local_or_ip_hostname("example.com")
    assert _scripts_in_label("a1\u0301") == {"Latin"}

    assert _char_script("ａ") == "Fullwidth"
    assert _char_script("α") == "Greek"
    assert _char_script("հ") == "Armenian"
    assert _char_script("中") == "EastAsian"
    assert _char_script("\u0301") == "Inherited"
    assert _char_script("1") == "Common"
    assert _char_script("ᚠ") == "Other"


def test_iter_string_values_flattens_nested_dicts_and_lists() -> None:
    values = iter_string_values(
        {
            "url": "https://example.com",
            "nested": {"text": "hello", "items": ["a", {"href": "b"}, ["deep"]]},
            "ignored": 123,
        }
    )

    assert values == [
        ("url", "https://example.com"),
        ("nested.text", "hello"),
        ("nested.items[0]", "a"),
        ("nested.items[1].href", "b"),
        ("nested.items[2][0]", "deep"),
    ]


def test_looks_like_url_key_checks_leaf_key_names() -> None:
    assert looks_like_url_key("payload.items[0].href")
    assert looks_like_url_key("endpoint")
    assert not looks_like_url_key("payload.description")
