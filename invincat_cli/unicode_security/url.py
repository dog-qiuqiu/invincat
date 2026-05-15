"""URL and hostname safety checks for Unicode spoofing patterns."""

from __future__ import annotations

import ipaddress
import unicodedata
from urllib.parse import urlparse

from invincat_cli.unicode_security.dangerous import (
    detect_dangerous_unicode,
    summarize_issues,
)
from invincat_cli.unicode_security.models import UrlSafetyResult

CONFUSABLES: dict[str, str] = {
    "\u0430": "a",
    "\u0435": "e",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0443": "y",
    "\u0445": "x",
    "\u043d": "h",
    "\u0456": "i",
    "\u0458": "j",
    "\u043a": "k",
    "\u0455": "s",
    "\u03b1": "a",
    "\u03b5": "e",
    "\u03bf": "o",
    "\u03c1": "p",
    "\u03c7": "x",
    "\u03ba": "k",
    "\u03bd": "v",
    "\u03c4": "t",
    "\u0570": "h",
    "\u0578": "n",
    "\u057d": "u",
    "\uff41": "a",
    "\uff45": "e",
    "\uff4f": "o",
}

_URL_SAFE_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost"})


def check_url_safety(url: str) -> UrlSafetyResult:
    """Check a URL for suspicious Unicode and domain spoofing patterns."""
    warnings: list[str] = []
    suspicious = False

    issues = detect_dangerous_unicode(url)
    if issues:
        suspicious = True
        warnings.append(
            f"URL contains hidden Unicode characters ({summarize_issues(issues)})"
        )

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return UrlSafetyResult(not suspicious, None, tuple(warnings), tuple(issues))

    decoded_hostname, failed_punycode = _decode_hostname(hostname)
    decoded_domain = decoded_hostname if decoded_hostname != hostname else None
    if decoded_domain:
        warnings.append(f"Punycode domain decodes to '{decoded_domain}'")
    if failed_punycode:
        suspicious = True
        labels = ", ".join(failed_punycode)
        warnings.append(f"Punycode label(s) could not be decoded: {labels}")

    if _is_local_or_ip_hostname(decoded_hostname):
        return UrlSafetyResult(
            not suspicious, decoded_domain, tuple(warnings), tuple(issues)
        )

    for label in _split_hostname_labels(decoded_hostname):
        scripts = _scripts_in_label(label)
        if len(scripts) > 1:
            suspicious = True
            script_names = ", ".join(sorted(scripts))
            warnings.append(f"Domain label '{label}' mixes scripts ({script_names})")

        if _label_has_suspicious_confusable_mix(label):
            suspicious = True
            warnings.append(
                f"Domain label '{label}' contains confusable Unicode characters"
            )

    return UrlSafetyResult(
        not suspicious, decoded_domain, tuple(warnings), tuple(issues)
    )


def _decode_hostname(hostname: str) -> tuple[str, list[str]]:
    """Decode `xn--` punycode labels into Unicode labels when possible."""
    decoded_labels: list[str] = []
    failed_labels: list[str] = []
    for label in _split_hostname_labels(hostname):
        if label.startswith("xn--"):
            try:
                decoded_labels.append(label.encode("ascii").decode("idna"))
            except UnicodeError:
                decoded_labels.append(label)
                failed_labels.append(label)
            continue
        decoded_labels.append(label)
    return ".".join(decoded_labels), failed_labels


def _split_hostname_labels(hostname: str) -> list[str]:
    """Split a hostname into non-empty labels."""
    return [label for label in hostname.split(".") if label]


def _is_local_or_ip_hostname(hostname: str) -> bool:
    """Return whether hostname is localhost or an IP address literal."""
    host = hostname.strip().rstrip(".")
    if not host:
        return False
    if host.lower() in _URL_SAFE_LOCAL_HOSTS:
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _scripts_in_label(label: str) -> set[str]:
    """Collect non-common scripts used by a domain label."""
    scripts: set[str] = set()
    for character in label:
        script = _char_script(character)
        if script in {"Common", "Inherited"}:
            continue
        scripts.add(script)
    return scripts


def _label_has_suspicious_confusable_mix(label: str) -> bool:
    """Return whether a label has likely deceptive confusable characters."""
    if not any(character in CONFUSABLES for character in label):
        return False
    scripts = _scripts_in_label(label)
    return len(scripts) > 1


def _char_script(character: str) -> str:
    """Classify a character into a coarse Unicode script bucket."""
    name = unicodedata.name(character, "")
    category = unicodedata.category(character)

    if "FULLWIDTH LATIN" in name:
        return "Fullwidth"
    if "LATIN" in name:
        return "Latin"
    if "CYRILLIC" in name:
        return "Cyrillic"
    if "GREEK" in name:
        return "Greek"
    if "ARMENIAN" in name:
        return "Armenian"
    if any(
        token in name
        for token in (
            "CJK",
            "HIRAGANA",
            "KATAKANA",
            "HANGUL",
            "BOPOMOFO",
            "IDEOGRAPHIC",
        )
    ):
        return "EastAsian"
    if category.startswith("M"):
        return "Inherited"
    if category[0] in {"N", "P", "S", "Z", "C"}:
        return "Common"
    return "Other"
