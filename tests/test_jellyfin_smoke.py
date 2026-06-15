import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import jellyfin_smoke


def test_run_smoke_skips_when_jellyfin_url_and_auth_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    monkeypatch.delenv("JELLYFIN_USERNAME", raising=False)
    monkeypatch.delenv("JELLYFIN_PASSWORD", raising=False)
    monkeypatch.delenv("JELLYFIN_SHORTS_DIR", raising=False)

    with pytest.raises(pytest.skip.Exception, match="JELLYFIN_BASE_URL"):
        jellyfin_smoke.run_smoke()


def test_select_video_candidate_returns_skip_ready_result_when_no_default_or_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("JELLYFIN_SHORTS_DIR", raising=False)

    result = jellyfin_smoke.select_video_candidate(default_shorts_dir=tmp_path / "missing")

    assert result.path is None
    assert "JELLYFIN_SHORTS_DIR" in result.skip_reason


def test_select_video_candidate_prefers_override_with_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    override_dir = tmp_path / "shorts"
    override_dir.mkdir()
    video_path = override_dir / "clip.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setenv("JELLYFIN_SHORTS_DIR", str(override_dir))

    result = jellyfin_smoke.select_video_candidate(default_shorts_dir=tmp_path / "missing")

    assert result.skip_reason is None
    assert result.path == video_path


def test_select_video_candidate_filters_extensions_and_uses_deterministic_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    override_dir = tmp_path / "shorts"
    override_dir.mkdir()
    (override_dir / "z-last.txt").write_text("ignore me", encoding="utf-8")
    first_video = override_dir / "a-first.mkv"
    second_video = override_dir / "b-second.mp4"
    first_video.write_bytes(b"video one")
    second_video.write_bytes(b"video two")

    monkeypatch.setenv("JELLYFIN_SHORTS_DIR", str(override_dir))

    result = jellyfin_smoke.select_video_candidate(default_shorts_dir=tmp_path / "missing")

    assert result.skip_reason is None
    assert result.path == first_video


def test_get_bvf_path_maps_movie_to_sibling_bvf():
    movie_path = Path("/path/Movie.mp4")

    assert jellyfin_smoke.get_bvf_path(movie_path) == Path("/path/Movie.bvf")


def test_build_analyzer_command_uses_existing_demo_branch_analyzer(tmp_path: Path):
    movie_path = tmp_path / "Movie.mp4"
    output_dir = tmp_path / "library"

    command = jellyfin_smoke.build_analyzer_command(movie_path, output_dir)

    assert command[:3] == [sys.executable, "analyzer/analyze.py", str(movie_path)]
    assert "--demo-branch" in command
    assert "--output-dir" in command
    assert command[-1] == str(output_dir)
