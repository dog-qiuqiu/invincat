from __future__ import annotations

from pathlib import Path

from invincat_cli.io.input import (
    MediaTracker,
    _extract_unquoted_leading_path_with_spaces,
    _leading_token_end,
    _normalize_posix_pasted_path,
    _normalize_unicode_spaces,
    _resolve_existing_pasted_path,
    _resolve_with_unicode_space_variants,
    _split_paste_line,
    _token_to_path,
    extract_leading_pasted_file_path,
    normalize_pasted_path,
    parse_file_mentions,
    parse_pasted_file_paths,
    parse_pasted_path_payload,
    parse_single_pasted_file_path,
)
from invincat_cli.io.media_utils import ImageData, VideoData


def test_media_tracker_adds_gets_syncs_and_clears_media() -> None:
    tracker = MediaTracker()
    image_one = ImageData(base64_data="a", format="png", placeholder="")
    image_two = ImageData(base64_data="b", format="png", placeholder="")
    video = VideoData(base64_data="c", format="mp4", placeholder="")

    assert tracker.add_image(image_one) == "[image 1]"
    assert tracker.add_image(image_two) == "[image 2]"
    assert tracker.add_video(video) == "[video 1]"
    assert tracker.get_images() == [image_one, image_two]
    assert tracker.get_videos() == [video]
    assert tracker.get_media("image") == [image_one, image_two]
    assert tracker.get_media("video") == [video]

    tracker.sync_to_text("keep [image 2] and [video 1]")
    assert tracker.get_images() == [image_two]
    assert tracker.next_image_id == 3
    assert tracker.get_videos() == [video]
    assert tracker.next_video_id == 2

    tracker.sync_to_text("no placeholders")
    assert tracker.get_images() == []
    assert tracker.get_videos() == []
    assert tracker.next_image_id == 1
    assert tracker.next_video_id == 1


def test_parse_file_mentions_resolves_files_and_skips_email(
    monkeypatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "docs" / "read me.txt"
    file_path.parent.mkdir()
    file_path.write_text("content")
    monkeypatch.chdir(tmp_path)

    text, files = parse_file_mentions(
        r"read @docs/read\ me.txt and mail user@example.com plus @missing.txt"
    )

    assert text.startswith("read @docs")
    assert files == [file_path.resolve()]


def test_parse_file_mentions_handles_invalid_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def broken_resolve(self: Path) -> Path:
        raise OSError("bad path")

    monkeypatch.setattr(Path, "resolve", broken_resolve)

    assert parse_file_mentions("@file.txt") == ("@file.txt", [])


def test_parse_pasted_file_paths_accepts_quoted_escaped_and_file_urls(
    monkeypatch,
    tmp_path: Path,
) -> None:
    one = tmp_path / "one.txt"
    two = tmp_path / "two words.txt"
    three = tmp_path / "three.txt"
    for path in (one, two, three):
        path.write_text("content")
    monkeypatch.chdir(tmp_path)

    assert parse_pasted_file_paths(f"{one} '{two}'") == [
        one.resolve(),
        two.resolve(),
    ]
    assert parse_pasted_file_paths(f"{one}\n\n{three}") == [
        one.resolve(),
        three.resolve(),
    ]
    assert parse_pasted_file_paths(r"two\ words.txt") == [two.resolve()]
    assert parse_pasted_file_paths(three.as_uri()) == [three.resolve()]

    assert parse_pasted_file_paths("'unterminated") == []
    assert parse_pasted_file_paths(f"{one}\nmissing.txt") == []
    assert parse_pasted_file_paths("\n  \n") == []
    assert parse_pasted_file_paths("<>") == []
    assert parse_pasted_file_paths("") == []


def test_parse_single_and_normalize_pasted_paths(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "dir with spaces" / "file.txt"
    file_path.parent.mkdir()
    file_path.write_text("content")
    monkeypatch.chdir(tmp_path)

    assert normalize_pasted_path(f'"{file_path}"') == file_path
    assert normalize_pasted_path(f"'{file_path}'") == file_path
    assert normalize_pasted_path(file_path.as_uri()) == file_path
    assert normalize_pasted_path(str(file_path)) == file_path
    assert normalize_pasted_path("C:/Users/demo/file.txt") == Path(
        "C:/Users/demo/file.txt"
    )
    assert normalize_pasted_path(r"\\server\share\file.txt") == Path(
        r"\\server\share\file.txt"
    )
    assert normalize_pasted_path(r"C\:/Users/demo/file.txt") == Path(
        "C:/Users/demo/file.txt"
    )
    assert normalize_pasted_path("") is None
    assert normalize_pasted_path("<>") is None
    assert normalize_pasted_path("not a single path token") is None
    assert parse_single_pasted_file_path(str(file_path)) == file_path.resolve()
    assert parse_single_pasted_file_path("missing.txt") is None


def test_parse_pasted_path_payload_and_leading_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "prompt file.txt"
    file_path.write_text("content")
    monkeypatch.chdir(tmp_path)

    strict = parse_pasted_path_payload(str(file_path))
    assert strict is not None
    assert strict.paths == [file_path.resolve()]
    assert strict.token_end is None

    simple = tmp_path / "simple.txt"
    simple.write_text("content")
    strict_multi = parse_pasted_path_payload(str(simple))
    assert strict_multi is not None
    assert strict_multi.paths == [simple.resolve()]

    leading = parse_pasted_path_payload(
        f"  {file_path} please summarize",
        allow_leading_path=True,
    )
    assert leading is not None
    assert leading.paths == [file_path.resolve()]
    assert leading.token_end == 2 + len(str(file_path))

    assert parse_pasted_path_payload("missing prompt") is None
    assert parse_pasted_path_payload("missing prompt", allow_leading_path=True) is None
    assert extract_leading_pasted_file_path("") is None
    assert extract_leading_pasted_file_path('"unterminated') is None
    assert extract_leading_pasted_file_path(str(simple)) == (
        simple.resolve(),
        len(str(simple)),
    )


def test_extract_unquoted_leading_path_with_spaces(
    monkeypatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "file with spaces.txt"
    file_path.write_text("content")
    monkeypatch.chdir(tmp_path)

    result = extract_leading_pasted_file_path(f"{file_path} summarize it")

    assert result == (file_path.resolve(), len(str(file_path)))


def test_unicode_space_variant_resolution(monkeypatch, tmp_path: Path) -> None:
    real_path = tmp_path / "report\u00a0final.txt"
    real_path.write_text("content")
    monkeypatch.chdir(tmp_path)

    assert _normalize_unicode_spaces("report\u202ffinal") == "report final"
    assert _resolve_with_unicode_space_variants(Path("report final.txt")) == real_path
    assert (
        parse_single_pasted_file_path(str(tmp_path / "report final.txt"))
        == real_path.resolve()
    )


def test_low_level_token_helpers() -> None:
    assert _split_paste_line("'bad") == []
    assert _split_paste_line(r"one\ two") == ["one two"]
    assert _token_to_path("< file:///tmp/demo.txt >") == Path("/tmp/demo.txt")
    assert _token_to_path("<>") is None
    assert _token_to_path("") is None
    assert _token_to_path("file://localhost/tmp/demo.txt") == Path("/tmp/demo.txt")
    assert _token_to_path("file://server/share/demo.txt") == Path(
        "//server/share/demo.txt"
    )
    assert _token_to_path("file:///C:/Users/demo.txt") == Path("C:/Users/demo.txt")
    assert _token_to_path("file://") is None

    assert _leading_token_end('"quoted path" rest') == len('"quoted path"')
    assert _leading_token_end(r'"escaped \" quote" rest') == len(r'"escaped \" quote"')
    assert _leading_token_end("'unterminated") is None
    assert _leading_token_end("\\") == 1
    assert _leading_token_end(r"escaped\ space rest") == len(r"escaped\ space")
    assert _leading_token_end("plain") == len("plain")
    assert _leading_token_end("") is None


def test_leading_path_and_posix_fallback_guards(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert _extract_unquoted_leading_path_with_spaces("line\nbreak") is None
    assert _extract_unquoted_leading_path_with_spaces("relative path") is None
    assert _extract_unquoted_leading_path_with_spaces("/nospace") is None
    assert _extract_unquoted_leading_path_with_spaces("/missing path prompt") is None

    assert _normalize_posix_pasted_path("/tmp\nbad") is None
    assert _normalize_posix_pasted_path("~/demo") == Path("~/demo")


def test_resolve_existing_pasted_path_error_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class BadPath:
        def expanduser(self):
            return self

        def resolve(self):
            raise OSError("bad path")

    assert _resolve_existing_pasted_path(BadPath()) is None  # type: ignore[arg-type]

    missing = tmp_path / "missing.txt"

    class BadFuzzyPath:
        def resolve(self):
            raise OSError("bad fuzzy")

    monkeypatch.setattr(
        "invincat_cli.io.input._resolve_with_unicode_space_variants",
        lambda _path: BadFuzzyPath(),
    )
    assert _resolve_existing_pasted_path(missing) is None

    monkeypatch.setattr(
        "invincat_cli.io.input._resolve_with_unicode_space_variants",
        lambda _path: tmp_path,
    )
    assert _resolve_existing_pasted_path(missing) is None


def test_unicode_space_variant_resolution_edge_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    file_parent = tmp_path / "parent\u00a0file"
    file_parent.write_text("content")
    assert (
        _resolve_with_unicode_space_variants(
            tmp_path / "parent file" / "child name.txt"
        )
        is None
    )

    missing_dir = tmp_path / "missing parent"

    def bad_iterdir(self: Path):
        if self == tmp_path:
            raise OSError("cannot list")
        return iter(())

    monkeypatch.setattr(Path, "iterdir", bad_iterdir)
    assert _resolve_with_unicode_space_variants(missing_dir) is None
    monkeypatch.undo()

    real_dir = tmp_path / "folder\u00a0name"
    real_dir.mkdir()
    child = real_dir / "child\u00a0file.txt"
    child.write_text("content")
    assert (
        _resolve_with_unicode_space_variants(
            tmp_path / "folder name" / "child file.txt"
        )
        == child
    )
