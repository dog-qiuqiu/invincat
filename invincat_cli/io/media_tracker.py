"""Media placeholder tracking for chat input."""

from __future__ import annotations

import re
from typing import Literal

from invincat_cli.io.media_utils import ImageData, VideoData

MediaKind = Literal["image", "video"]

IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[image (?P<id>\d+)\]")
VIDEO_PLACEHOLDER_PATTERN = re.compile(r"\[video (?P<id>\d+)\]")


class MediaTracker:
    """Track pasted images and videos in the current conversation."""

    def __init__(self) -> None:
        self.images: list[ImageData] = []
        self.videos: list[VideoData] = []
        self.next_image_id: int = 1
        self.next_video_id: int = 1

    def add_media(self, data: ImageData | VideoData, kind: MediaKind) -> str:
        """Add a media item and return its placeholder text."""
        if kind == "image":
            placeholder = f"[image {self.next_image_id}]"
            data.placeholder = placeholder
            self.images.append(data)  # type: ignore[arg-type]
            self.next_image_id += 1
        else:
            placeholder = f"[video {self.next_video_id}]"
            data.placeholder = placeholder
            self.videos.append(data)  # type: ignore[arg-type]
            self.next_video_id += 1
        return placeholder

    def add_image(self, image_data: ImageData) -> str:
        """Add an image and return its placeholder text."""
        return self.add_media(image_data, "image")

    def add_video(self, video_data: VideoData) -> str:
        """Add a video and return its placeholder text."""
        return self.add_media(video_data, "video")

    def get_media(self, kind: MediaKind) -> list[ImageData] | list[VideoData]:
        """Get all tracked media of a given type."""
        if kind == "image":
            return list(self.images)
        return list(self.videos)

    def get_images(self) -> list[ImageData]:
        """Get all tracked images."""
        return list(self.images)

    def get_videos(self) -> list[VideoData]:
        """Get all tracked videos."""
        return list(self.videos)

    def clear(self) -> None:
        """Clear all tracked media and reset counters."""
        self.images.clear()
        self.videos.clear()
        self.next_image_id = 1
        self.next_video_id = 1

    def sync_to_text(self, text: str) -> None:
        """Retain only media still referenced by placeholders in current text."""
        img_found = self._sync_kind_images(text)
        vid_found = self._sync_kind_videos(text)
        if not img_found and not vid_found:
            self.clear()

    def _sync_kind_images(self, text: str) -> bool:
        placeholders = {m.group(0) for m in IMAGE_PLACEHOLDER_PATTERN.finditer(text)}
        self.images = [img for img in self.images if img.placeholder in placeholders]
        if not self.images:
            self.next_image_id = 1
        else:
            self.next_image_id = self._max_placeholder_id(
                self.images, IMAGE_PLACEHOLDER_PATTERN, len(self.images)
            )
        return bool(placeholders)

    def _sync_kind_videos(self, text: str) -> bool:
        placeholders = {m.group(0) for m in VIDEO_PLACEHOLDER_PATTERN.finditer(text)}
        self.videos = [vid for vid in self.videos if vid.placeholder in placeholders]
        if not self.videos:
            self.next_video_id = 1
        else:
            self.next_video_id = self._max_placeholder_id(
                self.videos, VIDEO_PLACEHOLDER_PATTERN, len(self.videos)
            )
        return bool(placeholders)

    @staticmethod
    def _max_placeholder_id(
        items: list[ImageData] | list[VideoData],
        pattern: re.Pattern[str],
        fallback_count: int,
    ) -> int:
        max_id = 0
        for item in items:
            match = pattern.fullmatch(item.placeholder)
            if match is not None:
                max_id = max(max_id, int(match.group("id")))
        return max_id + 1 if max_id else fallback_count + 1
