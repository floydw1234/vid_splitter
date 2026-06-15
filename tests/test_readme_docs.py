from pathlib import Path


def test_root_readme_documents_fast_python_tests_csharp_reference_and_optional_smoke():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "README.md"

    contents = readme_path.read_text(encoding="utf-8")

    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in contents
    assert "tests/test_bvf_muxer.py" in contents
    assert "csharp_plugin/README.md" in contents or "dotnet test SmartBranching.Plugin.sln -c Release" in contents
    assert "RUN_REAL_ANALYZER_TESTS=1" in contents
    assert "tests/test_real_video_integration.py" in contents
