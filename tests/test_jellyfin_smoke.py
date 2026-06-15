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
