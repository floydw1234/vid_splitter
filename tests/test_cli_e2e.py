import json
import subprocess
import sys
from pathlib import Path

import pytest

from vid_splitter.bvf_muxer import BvfMuxer


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


@pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0
    or subprocess.run(["which", "ffprobe"], capture_output=True).returncode != 0,
    reason="ffmpeg/ffprobe are required for CLI E2E smoke test",
)
def test_analyze_bvf_and_resolve_from_user_json(tmp_path: Path):
    video = tmp_path / "demo.mp4"
    _run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "24", "-keyint_min", "24",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        str(video),
    ])

    analyze = _run([
        sys.executable, "analyzer/analyze.py", str(video),
        "--demo-branch", "--output-dir", str(tmp_path),
    ])
    assert "BVF:" in analyze.stdout

    bvf = tmp_path / "demo.bvf"
    assert bvf.exists()
    parsed = BvfMuxer.read_bvf(bvf)
    assert parsed["header"]["segment_count"] == 3
    assert parsed["manifest"]["segments"][1]["risk"] == "mature"

    child_json = tmp_path / "child.json"
    adult_json = tmp_path / "adult.json"
    child_json.write_text(json.dumps({"birthday": "2016-01-01", "sex": "female"}), encoding="utf-8")
    adult_json.write_text(json.dumps({"birthday": "1988-01-01", "sex": "female"}), encoding="utf-8")

    child = _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(child_json), "--dry-run"])
    adult = _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(adult_json), "--dry-run"])
    assert "Profile: child" in child.stdout
    assert "Total segments: 2" in child.stdout
    assert "seg_002" not in child.stdout
    assert "Profile: adult" in adult.stdout
    assert "Total segments: 3" in adult.stdout
    assert "seg_002" in adult.stdout

    child_export = tmp_path / "child.mp4"
    adult_export = tmp_path / "adult.mp4"
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(child_json), "--export", str(child_export)])
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(adult_json), "--export", str(adult_export)])
    assert child_export.stat().st_size > 0
    assert adult_export.stat().st_size > child_export.stat().st_size
