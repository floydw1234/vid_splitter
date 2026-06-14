from pathlib import Path


def test_csharp_plugin_readme_contains_smoke_10_html_comment():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"

    contents = readme_path.read_text(encoding="utf-8")

    assert "<!--" in contents and "-->" in contents
    assert "concurrency smoke ticket 10" in contents.lower()
