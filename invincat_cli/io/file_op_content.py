"""Content loading helpers for file operation tracking."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli.io.file_op_models import FileOperationRecord

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

logger = logging.getLogger(__name__)


def read_before_content(
    backend: BackendProtocol | None,
    record: FileOperationRecord,
    path_str: str,
    safe_read: Callable[[Path], str | None],
) -> str | None:
    """Read initial content before a write/edit operation."""
    if backend and path_str:
        try:
            responses = backend.download_files([path_str])
            if (
                responses
                and responses[0].content is not None
                and responses[0].error is None
            ):
                return responses[0].content.decode("utf-8")
            return ""
        except (OSError, UnicodeDecodeError, AttributeError) as e:
            logger.debug("Failed to read before_content for %s: %s", path_str, e)
            return ""
    if record.physical_path:
        return safe_read(record.physical_path) or ""
    return None


def populate_after_content(
    backend: BackendProtocol | None,
    record: FileOperationRecord,
    safe_read: Callable[[Path], str | None],
) -> None:
    """Populate updated content after a write/edit operation."""
    logger.debug(
        "_populate_after_content: tool=%s, path=%s, physical_path=%s, backend=%s",
        record.tool_name,
        record.args.get("file_path") or record.args.get("path"),
        record.physical_path,
        "available" if backend else "not available",
    )

    if backend and _try_backend_after_content(backend, record):
        return
    if record.physical_path is None:
        logger.debug(
            "No physical_path for %s, cannot read from local filesystem",
            record.args.get("file_path") or record.args.get("path"),
        )
        record.after_content = None
        return

    record.after_content = safe_read(record.physical_path)
    if record.after_content is not None:
        logger.debug(
            "Successfully read after_content from local filesystem: %s",
            record.physical_path,
        )


def _try_backend_after_content(
    backend: BackendProtocol,
    record: FileOperationRecord,
) -> bool:
    try:
        file_path = record.args.get("file_path") or record.args.get("path")
        if not file_path:
            return False
        logger.debug("Attempting backend download for: %s", file_path)
        responses = backend.download_files([file_path])
        if (
            responses
            and responses[0].content is not None
            and responses[0].error is None
        ):
            record.after_content = responses[0].content.decode("utf-8")
            logger.debug(
                "Backend download successful, content length: %s",
                len(record.after_content),
            )
            return True
        logger.debug(
            "Backend download failed for %s (responses: %s), trying local filesystem",
            file_path,
            responses,
        )
    except (OSError, UnicodeDecodeError, AttributeError) as e:
        logger.debug(
            "Backend read failed for %s: %s, trying local filesystem",
            record.args.get("file_path") or record.args.get("path"),
            e,
            exc_info=True,
        )
    return False
