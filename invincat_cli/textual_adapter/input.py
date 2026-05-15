"""Input normalization helpers for Textual agent execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from invincat_cli.io.input import MediaTracker


async def build_message_content(
    user_input: str,
    image_tracker: MediaTracker | None,
    *,
    parse_file_mentions_func: Callable[[str], tuple[str, list[Path]]],
    read_mentioned_file_func: Callable[[Path, int], str],
    create_multimodal_content_func: Callable[[str, list[Any], list[Any]], Any],
    max_embed_bytes: int = 256 * 1024,
) -> Any:  # noqa: ANN401
    """Build the user message content sent to the LangGraph stream."""
    prompt_text, mentioned_files = await asyncio.to_thread(
        parse_file_mentions_func, user_input
    )

    if mentioned_files:
        context_parts = [prompt_text, "\n\n## Referenced Files\n"]
        for file_path in mentioned_files:
            try:
                part = await asyncio.to_thread(
                    read_mentioned_file_func,
                    file_path,
                    max_embed_bytes,
                )
                context_parts.append(part)
            except Exception as e:  # noqa: BLE001
                context_parts.append(
                    f"\n### {file_path.name}\n[Error reading file: {e}]"
                )
        final_input = "\n".join(context_parts)
    else:
        final_input = prompt_text

    images_to_send = []
    videos_to_send = []
    if image_tracker:
        images_to_send = image_tracker.get_images()
        videos_to_send = image_tracker.get_videos()

    if images_to_send or videos_to_send:
        message_content = create_multimodal_content_func(
            final_input,
            images_to_send,
            videos_to_send,
        )
    else:
        message_content = final_input

    if image_tracker:
        image_tracker.clear()

    return message_content
