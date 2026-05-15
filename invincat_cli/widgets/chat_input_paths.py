"""Dropped-path and media attachment helpers for chat input."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli.i18n import t
from invincat_cli.widgets.chat_text_area import ChatTextArea

if TYPE_CHECKING:
    from invincat_cli.io.input import ParsedPastedPathPayload

logger = logging.getLogger(__name__)


class ChatInputPathMixin:
    """Handle pasted file paths and media-placeholder replacement."""

    @staticmethod
    def _parse_dropped_path_payload(
        text: str, *, allow_leading_path: bool = False
    ) -> ParsedPastedPathPayload | None:
        """Parse dropped-path payload text through a single parser entrypoint.

        Returns:
            Parsed payload details, otherwise `None`.
        """
        from invincat_cli.io.input import parse_pasted_path_payload

        return parse_pasted_path_payload(text, allow_leading_path=allow_leading_path)

    def _parse_dropped_path_payload_with_command_recovery(
        self, text: str, *, allow_leading_path: bool = False
    ) -> tuple[str, ParsedPastedPathPayload | None]:
        """Parse payload and recover stripped leading slash in command mode.

        Args:
            text: Input text to parse.
            allow_leading_path: Whether to parse leading path + suffix payloads.

        Returns:
            Tuple of `(candidate_text, parsed_payload)`.
        """
        candidate = text
        parsed = self._parse_dropped_path_payload(
            text, allow_leading_path=allow_leading_path
        )
        if parsed is not None:
            return candidate, parsed

        if self.mode != "command":
            return candidate, None

        prefixed = f"/{text.lstrip('/')}"
        parsed = self._parse_dropped_path_payload(
            prefixed, allow_leading_path=allow_leading_path
        )
        if parsed is None:
            return candidate, None

        logger.debug(
            "Recovering stripped absolute path; resetting mode from "
            "'command' to 'normal'"
        )
        self.mode = "normal"
        return prefixed, parsed

    def _extract_leading_dropped_path_with_command_recovery(
        self, text: str
    ) -> tuple[str, tuple[Path, int] | None]:
        """Extract a leading dropped-path token with command-mode recovery.

        Args:
            text: Input text to parse.

        Returns:
            Tuple of `(candidate_text, leading_match)`, where `leading_match` is
            `(path, token_end)` when extraction succeeds, otherwise `None`.
        """
        from invincat_cli.io.input import extract_leading_pasted_file_path

        leading_match = extract_leading_pasted_file_path(text)
        candidate = text
        if leading_match is not None:
            return candidate, leading_match

        if self.mode != "command":
            return candidate, None

        prefixed = f"/{text.lstrip('/')}"
        leading_match = extract_leading_pasted_file_path(prefixed)
        if leading_match is None:
            return candidate, None

        logger.debug(
            "Recovering stripped absolute leading path; resetting mode "
            "from 'command' to 'normal'"
        )
        self.mode = "normal"
        return prefixed, leading_match

    @staticmethod
    def _is_existing_path_payload(text: str) -> bool:
        """Return whether text is a dropped-path payload for existing files."""
        if len(text) < 2:  # noqa: PLR2004  # Need at least '/' + one char
            return False
        from invincat_cli.io.input import parse_pasted_path_payload

        return parse_pasted_path_payload(text, allow_leading_path=True) is not None

    def _is_dropped_path_payload(self, text: str) -> bool:
        """Return whether current text looks like a dropped file-path payload."""
        if not text:
            return False
        if self._is_existing_path_payload(text):
            return True
        if self.mode == "command":
            candidate = f"/{text.lstrip('/')}"
            return self._is_existing_path_payload(candidate)
        return False

    def _sync_media_tracker_to_text(self, text: str) -> None:
        """Keep tracked media aligned with placeholder tokens in input text.

        Args:
            text: Current text in the input area.
        """
        if not self._image_tracker:
            return
        if self._skip_media_sync_events:
            if self._skip_media_sync_events < 0:
                logger.warning(
                    "_skip_media_sync_events is negative (%d); resetting to 0",
                    self._skip_media_sync_events,
                )
                self._skip_media_sync_events = 0
            else:
                self._skip_media_sync_events -= 1
            return
        self._image_tracker.sync_to_text(text)

    def on_chat_text_area_pasted_paths(self, event: ChatTextArea.PastedPaths) -> None:
        """Handle paste payloads that resolve to dropped file paths."""
        if not self._text_area:
            return

        self._insert_pasted_paths(event.raw_text, event.paths)

    def handle_external_paste(self, pasted: str) -> bool:
        """Handle paste text from app-level routing when input is not focused.

        When the text area is mounted, the paste is always consumed: file paths
        are attached as images, and plain text is inserted directly.

        Args:
            pasted: Raw pasted text payload.

        Returns:
            `True` when the text area is mounted and the paste was inserted,
                `False` if the widget is not yet composed.
        """
        if not self._text_area:
            return False

        parsed = self._parse_dropped_path_payload(pasted)
        if parsed is None:
            self._text_area.insert(pasted)
        else:
            self._insert_pasted_paths(pasted, parsed.paths)

        self._text_area.focus()
        return True

    def _apply_inline_dropped_path_replacement(self, text: str) -> bool:
        """Replace full dropped-path payload text with image placeholders.

        Some terminals insert drag-and-drop payloads as plain text rather than
        dispatching a dedicated paste event. When the current text resolves to
        one or more file paths and at least one path is an image, rewrite the
        text inline to `[image N]` placeholders.

        Args:
            text: Current text area content.

        Returns:
            `True` if text was rewritten inline, otherwise `False`.
        """
        if not self._text_area:
            return False

        parsed = self._parse_dropped_path_payload(text)
        if parsed is None:
            return False

        replacement, attached = self._build_path_replacement(
            text, parsed.paths, add_trailing_space=True
        )
        if not attached or replacement == text:
            return False

        self._applying_inline_path_replacement = True
        self._text_area.text = replacement
        lines = replacement.split("\n")
        self._text_area.move_cursor((len(lines) - 1, len(lines[-1])))
        return True

    def _insert_pasted_paths(self, raw_text: str, paths: list[Path]) -> None:
        """Insert pasted path payload, attaching images when possible.

        Args:
            raw_text: Original paste payload text.
            paths: Resolved file paths parsed from the payload.
        """
        if not self._text_area:
            return
        replacement, attached = self._build_path_replacement(
            raw_text, paths, add_trailing_space=True
        )
        if attached:
            self._text_area.insert(replacement)
            return
        self._text_area.insert(raw_text)

    def _build_path_replacement(
        self,
        raw_text: str,
        paths: list[Path],
        *,
        add_trailing_space: bool,
    ) -> tuple[str, bool]:
        """Build replacement text for dropped paths and attach any images.

        Args:
            raw_text: Original paste payload text.
            paths: Resolved file paths parsed from the payload.
            add_trailing_space: Whether to append a trailing space after the
                last token when paths are separated by spaces.

        Returns:
            Tuple of `(replacement, attached)` where `attached` indicates whether
            at least one media attachment (image or video) was created.
        """
        if not self._image_tracker:
            return raw_text, False

        from invincat_cli.io.media_utils import (
            IMAGE_EXTENSIONS,
            MAX_MEDIA_BYTES,
            VIDEO_EXTENSIONS,
            ImageData,
            get_media_from_path,
        )

        parts: list[str] = []
        attached = False
        for path in paths:
            media = get_media_from_path(path)
            if media is not None:
                kind = "image" if isinstance(media, ImageData) else "video"
                parts.append(self._image_tracker.add_media(media, kind))
                attached = True
                continue

            # Check if it looked like media but failed validation
            suffix = path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
                label = "Video" if suffix in VIDEO_EXTENSIONS else "Image"
                try:
                    size = path.stat().st_size
                    if size > MAX_MEDIA_BYTES:
                        msg = (
                            f"{label} too large: {path.name} "
                            f"({size // (1024 * 1024)} MB, max "
                            f"{MAX_MEDIA_BYTES // (1024 * 1024)} MB)"
                        )
                    else:
                        msg = f"Could not attach {label.lower()}: {path.name}"
                except OSError as exc:
                    logger.debug("Failed to stat media file %s: %s", path, exc)
                    msg = t("chat.attach_failed", type=label.lower(), name=path.name)
                self.app.notify(msg, severity="warning", timeout=5, markup=False)

            # Not a supported media file, keep as path
            logger.debug("Could not load media from dropped path: %s", path)
            parts.append(str(path))

        if not attached:
            return raw_text, False

        separator = "\n" if "\n" in raw_text else " "
        replacement = separator.join(parts)
        if separator == " " and add_trailing_space:
            replacement += " "
        return replacement, True

    def _replace_submitted_paths_with_images(self, value: str) -> str:
        """Replace dropped-path payloads in submitted text with image placeholders.

        Handles both full-path payloads and leading-path-with-suffix payloads
        (for example, `'<path>' what is this?`). When command mode previously
        stripped a leading slash, this method also retries with the slash
        restored before giving up.

        Args:
            value: Stripped submitted text (without mode prefix).

        Returns:
            Submitted text with image placeholders when attachment succeeded.
        """
        candidate, parsed = self._parse_dropped_path_payload_with_command_recovery(
            value, allow_leading_path=True
        )
        if parsed is None:
            return value

        if parsed.token_end is None:
            replacement, attached = self._build_path_replacement(
                candidate, parsed.paths, add_trailing_space=False
            )
            if attached:
                return replacement.strip()
            # Even when full-payload parsing resolves, still retry explicit
            # leading-token extraction before giving up.
            candidate, leading_match = (
                self._extract_leading_dropped_path_with_command_recovery(value)
            )
            if leading_match is None:
                return value
            leading_path, token_end = leading_match
        else:
            leading_path = parsed.paths[0]
            token_end = parsed.token_end

        replacement, attached = self._build_path_replacement(
            str(leading_path), [leading_path], add_trailing_space=False
        )
        if attached:
            suffix = candidate[token_end:].lstrip()
            if suffix:
                return f"{replacement.strip()} {suffix}".strip()
            return replacement.strip()
        return value

