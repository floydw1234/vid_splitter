from pathlib import Path


def test_csharp_plugin_readme_contains_smoke_10_html_comment():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"

    contents = readme_path.read_text(encoding="utf-8")

    assert "<!--" in contents and "-->" in contents
    assert "concurrency smoke ticket 10" in contents.lower()


def test_csharp_plugin_readme_documents_release_build_test_and_optional_smoke_checks():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"

    contents = readme_path.read_text(encoding="utf-8")

    assert "dotnet build SmartBranching.Plugin.sln -c Release" in contents
    assert "dotnet test SmartBranching.Plugin.sln -c Release" in contents
    assert "RUN_REAL_ANALYZER_TESTS=1" in contents
    assert "tests/test_real_video_integration.py" in contents
