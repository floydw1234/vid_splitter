from pathlib import Path


def test_test_pipeline_has_smoke_marker_near_header():
    repo_root = Path(__file__).resolve().parents[1]
    pipeline_path = repo_root / "test_pipeline.py"

    lines = pipeline_path.read_text(encoding="utf-8").splitlines()

    assert lines[12] == "# smoke comment marker"
    assert lines[13] == "import sys"
