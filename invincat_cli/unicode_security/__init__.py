"""Compatibility facade for Unicode security helpers."""

from __future__ import annotations

from invincat_cli.unicode_security.args import (
    URL_ARG_KEYS,
    iter_string_values,
    looks_like_url_key,
)
from invincat_cli.unicode_security.dangerous import (
    _DANGEROUS_CHARACTERS,
    _DANGEROUS_CODEPOINTS,
    _format_codepoint,
    _unicode_name,
    detect_dangerous_unicode,
    format_warning_detail,
    render_with_unicode_markers,
    strip_dangerous_unicode,
    summarize_issues,
)
from invincat_cli.unicode_security.models import UnicodeIssue, UrlSafetyResult
from invincat_cli.unicode_security.url import (
    CONFUSABLES,
    _char_script,
    _decode_hostname,
    _is_local_or_ip_hostname,
    _label_has_suspicious_confusable_mix,
    _scripts_in_label,
    _split_hostname_labels,
    check_url_safety,
)

__all__ = [
    "CONFUSABLES",
    "URL_ARG_KEYS",
    "UnicodeIssue",
    "UrlSafetyResult",
    "_DANGEROUS_CHARACTERS",
    "_DANGEROUS_CODEPOINTS",
    "_char_script",
    "_decode_hostname",
    "_format_codepoint",
    "_is_local_or_ip_hostname",
    "_label_has_suspicious_confusable_mix",
    "_scripts_in_label",
    "_split_hostname_labels",
    "_unicode_name",
    "check_url_safety",
    "detect_dangerous_unicode",
    "format_warning_detail",
    "iter_string_values",
    "looks_like_url_key",
    "render_with_unicode_markers",
    "strip_dangerous_unicode",
    "summarize_issues",
]
