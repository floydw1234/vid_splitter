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


def test_csharp_plugin_readme_documents_packaged_zip_workflow():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"
    contents = readme_path.read_text(encoding="utf-8")
    lowered = contents.lower()

    assert "package_plugin.py" in contents
    assert ".zip" in contents
    assert "build output" in lowered or "--build-output" in contents


def test_csharp_plugin_readme_documents_repository_and_offline_install_flows():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"
    contents = readme_path.read_text(encoding="utf-8")
    lowered = contents.lower()

    assert "repository" in lowered
    assert "manifest.json" in contents
    assert "offline" in lowered
    assert ".zip" in contents


def test_csharp_plugin_readme_documents_update_and_uninstall_steps():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"
    contents = readme_path.read_text(encoding="utf-8")
    lowered = contents.lower()

    assert "update" in lowered
    assert "uninstall" in lowered
    assert "restart jellyfin" in lowered or "restart the jellyfin server" in lowered


def test_csharp_plugin_readme_documents_runtime_artifacts_and_metadata_source():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"
    contents = readme_path.read_text(encoding="utf-8")

    assert "Jellyfin.Plugin.SmartBranching.dll" in contents
    assert "Jellyfin.Plugin.SmartBranching.deps.json" in contents
    assert "ZstdSharp.dll" in contents
    assert "build.yaml" in contents


def test_csharp_plugin_readme_no_longer_uses_manual_file_copy_as_primary_install_flow():
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = repo_root / "csharp_plugin" / "README.md"
    contents = readme_path.read_text(encoding="utf-8")
    lowered = contents.lower()

    assert "install is manual today" not in lowered
    assert "sudo cp bin/release/net8.0/jellyfin.plugin.smartbranching.dll" not in lowered
    assert "fallback" in lowered or "development-only" in lowered or "dev-only" in lowered
