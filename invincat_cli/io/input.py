"""Input handling facade for file mentions, pasted paths, and media tracking."""

from __future__ import annotations

from invincat_cli.io.file_mentions import (
    EMAIL_PREFIX_PATTERN as EMAIL_PREFIX_PATTERN,
)
from invincat_cli.io.file_mentions import (
    FILE_MENTION_PATTERN as FILE_MENTION_PATTERN,
)
from invincat_cli.io.file_mentions import (
    INPUT_HIGHLIGHT_PATTERN as INPUT_HIGHLIGHT_PATTERN,
)
from invincat_cli.io.file_mentions import (
    PATH_CHAR_CLASS as PATH_CHAR_CLASS,
)
from invincat_cli.io.file_mentions import (
    parse_file_mentions as parse_file_mentions,
)
from invincat_cli.io.media_tracker import (
    IMAGE_PLACEHOLDER_PATTERN as IMAGE_PLACEHOLDER_PATTERN,
)
from invincat_cli.io.media_tracker import (
    VIDEO_PLACEHOLDER_PATTERN as VIDEO_PLACEHOLDER_PATTERN,
)
from invincat_cli.io.media_tracker import (
    MediaKind as MediaKind,
)
from invincat_cli.io.media_tracker import (
    MediaTracker as MediaTracker,
)
from invincat_cli.io.pasted_paths import (
    ParsedPastedPathPayload as ParsedPastedPathPayload,
)
from invincat_cli.io.pasted_paths import (
    _extract_unquoted_leading_path_with_spaces as _extract_unquoted_leading_path_with_spaces,
)
from invincat_cli.io.pasted_paths import (
    _leading_token_end as _leading_token_end,
)
from invincat_cli.io.pasted_paths import (
    _normalize_posix_pasted_path as _normalize_posix_pasted_path,
)
from invincat_cli.io.pasted_paths import (
    _normalize_unicode_spaces as _normalize_unicode_spaces,
)
from invincat_cli.io.pasted_paths import (
    _normalize_windows_pasted_path as _normalize_windows_pasted_path,
)
from invincat_cli.io.pasted_paths import (
    _resolve_existing_pasted_path as _resolve_existing_pasted_path,
)
from invincat_cli.io.pasted_paths import (
    _resolve_with_unicode_space_variants as _resolve_with_unicode_space_variants,
)
from invincat_cli.io.pasted_paths import (
    _split_paste_line as _split_paste_line,
)
from invincat_cli.io.pasted_paths import (
    _token_to_path as _token_to_path,
)
from invincat_cli.io.pasted_paths import (
    extract_leading_pasted_file_path as extract_leading_pasted_file_path,
)
from invincat_cli.io.pasted_paths import (
    normalize_pasted_path as normalize_pasted_path,
)
from invincat_cli.io.pasted_paths import (
    parse_pasted_file_paths as parse_pasted_file_paths,
)
from invincat_cli.io.pasted_paths import (
    parse_pasted_path_payload as parse_pasted_path_payload,
)
from invincat_cli.io.pasted_paths import (
    parse_single_pasted_file_path as parse_single_pasted_file_path,
)

__all__ = [
    "EMAIL_PREFIX_PATTERN",
    "FILE_MENTION_PATTERN",
    "IMAGE_PLACEHOLDER_PATTERN",
    "INPUT_HIGHLIGHT_PATTERN",
    "MediaKind",
    "MediaTracker",
    "PATH_CHAR_CLASS",
    "ParsedPastedPathPayload",
    "VIDEO_PLACEHOLDER_PATTERN",
    "extract_leading_pasted_file_path",
    "normalize_pasted_path",
    "parse_file_mentions",
    "parse_pasted_file_paths",
    "parse_pasted_path_payload",
    "parse_single_pasted_file_path",
]
