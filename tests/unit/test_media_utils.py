from __future__ import annotations

import base64
import pathlib
import subprocess
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from invincat_cli.io import media_utils


def write_png(path) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color=(255, 0, 0)).save(buffer, format="PNG")
    data = buffer.getvalue()
    path.write_bytes(data)
    return data


def test_image_data_to_message_content() -> None:
    image = media_utils.ImageData("abc", "png", "[image]")

    assert image.to_message_content() == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }


def test_video_data_to_message_content() -> None:
    video = media_utils.VideoData("abc", "mp4", "[video]")

    block = video.to_message_content()
    assert block["type"] == "video"
    assert block["base64"] == "abc"
    assert block["mime_type"] == "video/mp4"


def test_encode_to_base64() -> None:
    assert media_utils.encode_to_base64(b"hello") == base64.b64encode(b"hello").decode(
        "utf-8"
    )


def test_get_executable_delegates_to_shutil(monkeypatch) -> None:
    monkeypatch.setattr(media_utils.shutil, "which", lambda name: f"/bin/{name}")

    assert media_utils._get_executable("tool") == "/bin/tool"


def test_detect_video_format_from_magic_bytes() -> None:
    assert media_utils._detect_video_format(b"\x00\x00\x00\x18ftypisom") == "mp4"
    assert media_utils._detect_video_format(b"\x00\x00\x00\x18ftypqt  ") == (
        "quicktime"
    )
    assert media_utils._detect_video_format(b"RIFF\x00\x00\x00\x00AVI ") == "avi"
    assert media_utils._detect_video_format(b"\x30\x26\xb2\x75wmv") == "x-ms-wmv"
    assert media_utils._detect_video_format(b"\x1a\x45\xdf\xa3webm") == "webm"
    assert media_utils._detect_video_format(b"not-video") is None


def test_get_image_from_path_encodes_valid_image(tmp_path) -> None:
    image_path = tmp_path / "sample.png"
    image_bytes = write_png(image_path)

    image = media_utils.get_image_from_path(image_path)

    assert image is not None
    assert image.format == "png"
    assert image.placeholder == "[image]"
    assert image.base64_data == media_utils.encode_to_base64(image_bytes)


def test_get_image_from_path_rejects_empty_and_invalid_files(tmp_path) -> None:
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")

    assert media_utils.get_image_from_path(empty) is None
    assert media_utils.get_image_from_path(invalid) is None


def test_get_image_from_path_handles_empty_read_and_format_fallbacks(
    monkeypatch,
    tmp_path,
) -> None:
    empty_read = tmp_path / "empty-read.png"
    empty_read.write_bytes(b"has-stat-size")
    original_read_bytes = pathlib.Path.read_bytes

    def fake_empty_read(self: pathlib.Path) -> bytes:
        if self == empty_read:
            return b""
        return original_read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", fake_empty_read)
    assert media_utils.get_image_from_path(empty_read) is None
    monkeypatch.setattr(pathlib.Path, "read_bytes", original_read_bytes)

    jpg_path = tmp_path / "sample.jpg"
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="JPEG")
    jpg_bytes = buffer.getvalue()
    jpg_path.write_bytes(jpg_bytes)

    jpg_image = media_utils.get_image_from_path(jpg_path)
    assert jpg_image is not None
    assert jpg_image.format == "jpeg"

    class FakeJpgImage:
        format = "jpg"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    fake_jpg = tmp_path / "fake-jpg"
    fake_jpg.write_bytes(b"fake")
    monkeypatch.setattr(Image, "open", lambda _fp: FakeJpgImage())
    normalized = media_utils.get_image_from_path(fake_jpg)
    assert normalized is not None
    assert normalized.format == "jpeg"

    class FakeImage:
        format = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    no_suffix = tmp_path / "no_suffix"
    no_suffix.write_bytes(b"fake")
    monkeypatch.setattr(Image, "open", lambda _fp: FakeImage())

    image = media_utils.get_image_from_path(no_suffix)
    assert image is not None
    assert image.format == "png"


def test_get_image_from_path_rejects_large_or_unreadable_files(
    monkeypatch, tmp_path
) -> None:
    image_path = tmp_path / "large.png"
    image_path.write_bytes(b"not actually large")
    original_stat = pathlib.Path.stat

    def fake_stat(self: pathlib.Path) -> SimpleNamespace:
        if self == image_path:
            return SimpleNamespace(st_size=media_utils.MAX_MEDIA_BYTES + 1)
        return original_stat(self)

    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)

    assert media_utils.get_image_from_path(image_path) is None

    unreadable = tmp_path / "unreadable.png"
    unreadable.write_bytes(b"data")
    original_read_bytes = pathlib.Path.read_bytes

    def fake_read_bytes(self: pathlib.Path) -> bytes:
        if self == unreadable:
            raise OSError("cannot read")
        return original_read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", fake_read_bytes)

    assert media_utils.get_image_from_path(unreadable) is None


def test_get_video_from_path_detects_format_from_content(tmp_path) -> None:
    video_path = tmp_path / "renamed.mov"
    video_bytes = b"\x00\x00\x00\x18ftypisom" + b"payload"
    video_path.write_bytes(video_bytes)

    video = media_utils.get_video_from_path(video_path)

    assert video is not None
    assert video.format == "mp4"
    assert video.placeholder == "[video]"
    assert video.base64_data == media_utils.encode_to_base64(video_bytes)


def test_get_video_from_path_rejects_invalid_inputs(tmp_path) -> None:
    wrong_suffix = tmp_path / "video.txt"
    wrong_suffix.write_bytes(b"\x00\x00\x00\x18ftypisom")
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"short")
    invalid = tmp_path / "invalid.mp4"
    invalid.write_bytes(b"not-a-valid-signature")

    assert media_utils.get_video_from_path(wrong_suffix) is None
    assert media_utils.get_video_from_path(tiny) is None
    assert media_utils.get_video_from_path(invalid) is None


def test_get_video_from_path_rejects_empty_large_or_unreadable_files(
    monkeypatch,
    tmp_path,
) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert media_utils.get_video_from_path(empty) is None

    large = tmp_path / "large.mp4"
    large.write_bytes(b"\x00\x00\x00\x18ftypisom")
    original_stat = pathlib.Path.stat

    def fake_stat(self: pathlib.Path) -> SimpleNamespace:
        if self == large:
            return SimpleNamespace(st_size=media_utils.MAX_MEDIA_BYTES + 1)
        return original_stat(self)

    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)
    assert media_utils.get_video_from_path(large) is None

    unreadable = tmp_path / "unreadable.mp4"
    unreadable.write_bytes(b"\x00\x00\x00\x18ftypisom")
    original_read_bytes = pathlib.Path.read_bytes

    def fake_read_bytes(self: pathlib.Path) -> bytes:
        if self == unreadable:
            raise OSError("cannot read")
        return original_read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", fake_read_bytes)
    assert media_utils.get_video_from_path(unreadable) is None


def test_get_media_from_path_prefers_image_then_video(monkeypatch, tmp_path) -> None:
    path = tmp_path / "media.bin"
    image = media_utils.ImageData("image", "png", "[image]")
    video = media_utils.VideoData("video", "mp4", "[video]")

    monkeypatch.setattr(media_utils, "get_image_from_path", lambda _path: image)
    monkeypatch.setattr(media_utils, "get_video_from_path", lambda _path: video)

    assert media_utils.get_media_from_path(path) is image

    monkeypatch.setattr(media_utils, "get_image_from_path", lambda _path: None)
    assert media_utils.get_media_from_path(path) is video


def test_get_clipboard_image_is_quietly_unsupported_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(media_utils.sys, "platform", "linux")

    assert media_utils.get_clipboard_image() is None


def test_get_clipboard_image_routes_to_macos_helper(monkeypatch) -> None:
    image = media_utils.ImageData("img", "png", "[image]")
    monkeypatch.setattr(media_utils.sys, "platform", "darwin")
    monkeypatch.setattr(media_utils, "_get_macos_clipboard_image", lambda: image)

    assert media_utils.get_clipboard_image() is image


def test_get_clipboard_via_osascript_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(media_utils, "_get_executable", lambda _name: None)

    assert media_utils._get_clipboard_via_osascript() is None


def test_macos_clipboard_image_uses_pngpaste(monkeypatch) -> None:
    png_bytes = BytesIO()
    Image.new("RGB", (1, 1)).save(png_bytes, format="PNG")
    png_data = png_bytes.getvalue()
    monkeypatch.setattr(media_utils, "_get_executable", lambda name: "/bin/pngpaste")
    monkeypatch.setattr(
        media_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=png_data),
    )

    image = media_utils._get_macos_clipboard_image()

    assert image is not None
    assert image.format == "png"
    assert image.base64_data == media_utils.encode_to_base64(png_data)


def test_macos_clipboard_image_falls_back_when_pngpaste_fails(monkeypatch) -> None:
    monkeypatch.setattr(media_utils, "_get_executable", lambda name: "/bin/pngpaste")
    monkeypatch.setattr(
        media_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b""),
    )
    monkeypatch.setattr(
        media_utils,
        "_get_clipboard_via_osascript",
        lambda: media_utils.ImageData("fallback", "png", "[image]"),
    )

    assert media_utils._get_macos_clipboard_image().base64_data == "fallback"  # type: ignore[union-attr]


def test_macos_clipboard_image_handles_pngpaste_timeout(monkeypatch) -> None:
    monkeypatch.setattr(media_utils, "_get_executable", lambda name: "/bin/pngpaste")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("pngpaste", 2)

    monkeypatch.setattr(media_utils.subprocess, "run", timeout)
    monkeypatch.setattr(media_utils, "_get_clipboard_via_osascript", lambda: None)

    assert media_utils._get_macos_clipboard_image() is None


def test_macos_clipboard_image_handles_invalid_or_missing_pngpaste(
    monkeypatch,
) -> None:
    monkeypatch.setattr(media_utils, "_get_executable", lambda name: "/bin/pngpaste")
    monkeypatch.setattr(
        media_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"not-image"),
    )
    monkeypatch.setattr(media_utils, "_get_clipboard_via_osascript", lambda: None)
    assert media_utils._get_macos_clipboard_image() is None

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("pngpaste")

    monkeypatch.setattr(media_utils.subprocess, "run", missing)
    assert media_utils._get_macos_clipboard_image() is None


def test_clipboard_via_osascript_success_and_cleanup(monkeypatch, tmp_path) -> None:
    png_bytes = BytesIO()
    Image.new("RGB", (1, 1)).save(png_bytes, format="PNG")
    png_data = png_bytes.getvalue()
    temp_path = tmp_path / "clipboard.png"
    monkeypatch.setattr(
        media_utils, "_get_executable", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        media_utils.tempfile, "mkstemp", lambda suffix: (99, str(temp_path))
    )
    monkeypatch.setattr(media_utils.os, "close", lambda _fd: None)

    def fake_run(args, **_kwargs):
        script = args[-1]
        if script == "clipboard info":
            return SimpleNamespace(returncode=0, stdout="PNGf")
        temp_path.write_bytes(png_data)
        return SimpleNamespace(returncode=0, stdout="success")

    monkeypatch.setattr(media_utils.subprocess, "run", fake_run)

    image = media_utils._get_clipboard_via_osascript()

    assert image is not None
    assert image.format == "png"
    assert not temp_path.exists()


def test_clipboard_via_osascript_returns_none_for_non_image_or_failed_get(
    monkeypatch,
    tmp_path,
) -> None:
    temp_path = tmp_path / "clipboard.png"
    monkeypatch.setattr(
        media_utils, "_get_executable", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        media_utils.tempfile, "mkstemp", lambda suffix: (99, str(temp_path))
    )
    monkeypatch.setattr(media_utils.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        media_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="TEXT"),
    )

    assert media_utils._get_clipboard_via_osascript() is None

    def failed_get(args, **_kwargs):
        if args[-1] == "clipboard info":
            return SimpleNamespace(returncode=0, stdout="PNGf")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(media_utils.subprocess, "run", failed_get)
    assert media_utils._get_clipboard_via_osascript() is None


def test_clipboard_via_osascript_failure_and_tiff_paths(monkeypatch, tmp_path) -> None:
    temp_path = tmp_path / "clipboard.png"
    monkeypatch.setattr(
        media_utils, "_get_executable", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        media_utils.tempfile, "mkstemp", lambda suffix: (99, str(temp_path))
    )
    monkeypatch.setattr(media_utils.os, "close", lambda _fd: None)

    monkeypatch.setattr(
        media_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert media_utils._get_clipboard_via_osascript() is None

    tiff_bytes = BytesIO()
    Image.new("RGB", (1, 1), color=(0, 255, 0)).save(tiff_bytes, format="TIFF")
    tiff_data = tiff_bytes.getvalue()

    def tiff_run(args, **_kwargs):
        if args[-1] == "clipboard info":
            return SimpleNamespace(returncode=0, stdout="TIFF")
        assert "TIFF picture" in args[-1]
        temp_path.write_bytes(tiff_data)
        return SimpleNamespace(returncode=0, stdout="success")

    monkeypatch.setattr(media_utils.subprocess, "run", tiff_run)
    image = media_utils._get_clipboard_via_osascript()
    assert image is not None
    assert image.format == "png"


def test_clipboard_via_osascript_empty_invalid_timeout_and_cleanup_errors(
    monkeypatch,
    tmp_path,
) -> None:
    temp_path = tmp_path / "clipboard.png"
    monkeypatch.setattr(
        media_utils, "_get_executable", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        media_utils.tempfile, "mkstemp", lambda suffix: (99, str(temp_path))
    )
    monkeypatch.setattr(media_utils.os, "close", lambda _fd: None)

    def empty_file_run(args, **_kwargs):
        if args[-1] == "clipboard info":
            return SimpleNamespace(returncode=0, stdout="PNGf")
        temp_path.write_bytes(b"")
        return SimpleNamespace(returncode=0, stdout="success")

    monkeypatch.setattr(media_utils.subprocess, "run", empty_file_run)
    assert media_utils._get_clipboard_via_osascript() is None

    def invalid_file_run(args, **_kwargs):
        if args[-1] == "clipboard info":
            return SimpleNamespace(returncode=0, stdout="PNGf")
        temp_path.write_bytes(b"not-image")
        return SimpleNamespace(returncode=0, stdout="success")

    monkeypatch.setattr(media_utils.subprocess, "run", invalid_file_run)
    assert media_utils._get_clipboard_via_osascript() is None

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("osascript", 2)

    monkeypatch.setattr(media_utils.subprocess, "run", timeout)
    assert media_utils._get_clipboard_via_osascript() is None

    def os_error(*_args, **_kwargs):
        raise OSError("osascript failed")

    monkeypatch.setattr(media_utils.subprocess, "run", os_error)
    original_unlink = pathlib.Path.unlink

    def failing_unlink(self: pathlib.Path, *args, **kwargs) -> None:
        if self == temp_path:
            raise OSError("cleanup failed")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", failing_unlink)
    assert media_utils._get_clipboard_via_osascript() is None


def test_create_multimodal_content_includes_text_images_and_videos(monkeypatch) -> None:
    image = media_utils.ImageData("img", "png", "[image]")
    video = media_utils.VideoData("vid", "mp4", "[video]")
    monkeypatch.setattr(
        video,
        "to_message_content",
        lambda: {"type": "video", "source": "vid"},
    )

    assert media_utils.create_multimodal_content(" hi ", [image], [video]) == [
        {"type": "text", "text": " hi "},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,img"}},
        {"type": "video", "source": "vid"},
    ]
    assert media_utils.create_multimodal_content("  ", [], None) == []
