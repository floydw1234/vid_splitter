from pathlib import Path


def test_readme_docs_has_smoke_comment_marker_near_header():
    repo_root = Path(__file__).resolve().parents[1]
    test_path = repo_root / "tests" / "test_readme_docs.py"

    lines = test_path.read_text(encoding="utf-8").splitlines()

    assert "# smoke comment marker" in lines[:4]
