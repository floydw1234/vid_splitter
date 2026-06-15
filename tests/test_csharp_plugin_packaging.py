from pathlib import Path
import json
import hashlib
import subprocess
import sys
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_build_yaml() -> dict[str, object]:
    build_yaml_path = _repo_root() / "csharp_plugin" / "build.yaml"
    data: dict[str, object] = {}
    artifacts: list[str] = []
    current_key: str | None = None

    for raw_line in build_yaml_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("- "):
            if current_key == "artifacts":
                artifacts.append(stripped[2:].strip().strip('"'))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == ">":
            data[key] = ""
            continue
        data[key] = value.strip('"')

    data["artifacts"] = artifacts
    return data


def _read_directory_build_props_version() -> str:
    props_path = _repo_root() / "csharp_plugin" / "Directory.Build.props"
    root = ET.fromstring(props_path.read_text(encoding="utf-8"))
    version = root.findtext(".//Version")
    assert version is not None
    return version


def _find_manifest_path() -> Path | None:
    candidates = [
        _repo_root() / "manifest.json",
        _repo_root() / "csharp_plugin" / "manifest.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _read_manifest() -> list[dict[str, object]]:
    manifest_path = _find_manifest_path()
    assert manifest_path is not None, "Expected manifest.json to exist."
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _package_script_path() -> Path:
    return _repo_root() / "csharp_plugin" / "scripts" / "package_plugin.py"


def _deploy_script_path() -> Path:
    return _repo_root() / "csharp_plugin" / "scripts" / "deploy_vivo.py"


def _release_workflow_path() -> Path:
    return _repo_root() / ".github" / "workflows" / "release.yml"


def _write_fake_build_output(build_dir: Path, *, include_zstd: bool = True) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "Jellyfin.Plugin.SmartBranching.dll").write_bytes(b"fake-plugin-dll")
    (build_dir / "Jellyfin.Plugin.SmartBranching.deps.json").write_text(
        '{"runtimeTarget":{"name":"net8.0"}}',
        encoding="utf-8",
    )
    if include_zstd:
        (build_dir / "ZstdSharp.dll").write_bytes(b"fake-zstd-dll")


def _run_package_command(
    build_dir: Path,
    output_dir: Path,
    *,
    manifest_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(_package_script_path()),
        "--build-output",
        str(build_dir),
        "--output-dir",
        str(output_dir),
    ]
    if manifest_output is not None:
        command.extend(["--manifest-output", str(manifest_output)])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=_repo_root(),
    )


def _copy_manifest_for_packaging(temp_dir: Path) -> Path:
    source_manifest = _find_manifest_path()
    assert source_manifest is not None, "Expected manifest.json to exist."
    temp_manifest = temp_dir / "manifest.json"
    temp_manifest.write_text(source_manifest.read_text(encoding="utf-8"), encoding="utf-8")
    return temp_manifest


def _run_deploy_command(
    zip_path: Path,
    target_dir: Path,
    *,
    clean: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(_deploy_script_path()),
        "--package-zip",
        str(zip_path),
        "--target-dir",
        str(target_dir),
    ]
    if clean:
        command.append("--clean")
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=_repo_root(),
    )


def test_plugin_metadata_version_matches_directory_build_props():
    build_yaml = _read_build_yaml()
    props_version = _read_directory_build_props_version()

    assert build_yaml["version"] == props_version


def test_plugin_metadata_declares_required_runtime_artifacts():
    build_yaml = _read_build_yaml()
    artifacts = build_yaml["artifacts"]

    assert "Jellyfin.Plugin.SmartBranching.dll" in artifacts
    assert "Jellyfin.Plugin.SmartBranching.deps.json" in artifacts
    assert "ZstdSharp.dll" in artifacts


def test_repository_manifest_is_present_and_complete_if_checked_in():
    manifest = _read_manifest()
    assert isinstance(manifest, list)
    assert manifest

    plugin = manifest[0]
    for key in ("guid", "name", "overview", "description", "owner", "category", "versions"):
        assert key in plugin

    versions = plugin["versions"]
    assert isinstance(versions, list)
    assert versions

    latest = versions[0]
    for key in ("version", "targetAbi", "sourceUrl", "checksum", "timestamp"):
        assert key in latest


def test_package_command_creates_zip_from_declared_artifacts():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)

        result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)

        assert result.returncode == 0, result.stderr or result.stdout
        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1


def test_package_command_fails_when_declared_zstd_artifact_is_missing():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir, include_zstd=False)

        result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)

        assert result.returncode != 0
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "ZstdSharp.dll" in combined_output
        assert "missing" in combined_output.lower()


def test_package_command_fails_when_metadata_versions_do_not_match():
    build_yaml_path = _repo_root() / "csharp_plugin" / "build.yaml"
    original_text = build_yaml_path.read_text(encoding="utf-8")
    mutated_text = original_text.replace('version: "0.1.0.0"', 'version: "0.1.0.1"', 1)

    assert mutated_text != original_text, "Expected to mutate build.yaml version for the test."

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)
        build_yaml_path.write_text(mutated_text, encoding="utf-8")
        try:
            result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)
        finally:
            build_yaml_path.write_text(original_text, encoding="utf-8")

    assert result.returncode != 0
    combined_output = f"{result.stdout}\n{result.stderr}"
    assert "version" in combined_output.lower()
    assert "mismatch" in combined_output.lower()


def test_repository_manifest_version_entry_aligns_with_build_yaml():
    build_yaml = _read_build_yaml()
    manifest = _read_manifest()

    plugin = manifest[0]
    latest = plugin["versions"][0]

    assert latest["version"] == build_yaml["version"]
    assert latest["targetAbi"] == build_yaml["targetAbi"]


def test_repository_manifest_checksum_matches_packaged_zip_sha256():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)

        result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)
        assert result.returncode == 0, result.stderr or result.stdout

        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1
        zip_bytes = zip_files[0].read_bytes()
        expected_checksum = hashlib.sha256(zip_bytes).hexdigest()
        packaged_manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
        latest = packaged_manifest[0]["versions"][0]

        assert latest["checksum"] == expected_checksum


def test_deploy_helper_extracts_packaged_zip_into_target_directory():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        target_dir = tmp_path / "plugins" / "SmartBranching"
        target_dir.mkdir(parents=True)
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)

        package_result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)
        assert package_result.returncode == 0, package_result.stderr or package_result.stdout

        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1

        deploy_result = _run_deploy_command(zip_files[0], target_dir)

        assert deploy_result.returncode == 0, deploy_result.stderr or deploy_result.stdout
        assert (target_dir / "Jellyfin.Plugin.SmartBranching.dll").exists()
        assert (target_dir / "Jellyfin.Plugin.SmartBranching.deps.json").exists()
        assert (target_dir / "ZstdSharp.dll").exists()


def test_deploy_helper_can_clear_old_plugin_files_before_install():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        target_dir = tmp_path / "plugins" / "SmartBranching"
        target_dir.mkdir(parents=True)
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)
        (target_dir / "old-plugin.dll").write_bytes(b"stale")

        package_result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)
        assert package_result.returncode == 0, package_result.stderr or package_result.stdout

        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1

        deploy_result = _run_deploy_command(zip_files[0], target_dir, clean=True)

        assert deploy_result.returncode == 0, deploy_result.stderr or deploy_result.stdout
        assert not (target_dir / "old-plugin.dll").exists()
        assert (target_dir / "Jellyfin.Plugin.SmartBranching.dll").exists()


def test_deploy_helper_does_not_touch_unrelated_directories_when_cleaning():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        plugins_root = tmp_path / "plugins"
        target_dir = plugins_root / "SmartBranching"
        unrelated_dir = plugins_root / "OtherPlugin"
        target_dir.mkdir(parents=True)
        unrelated_dir.mkdir(parents=True)
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)
        (target_dir / "old-plugin.dll").write_bytes(b"stale")
        (unrelated_dir / "keep-me.txt").write_text("unchanged", encoding="utf-8")

        package_result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)
        assert package_result.returncode == 0, package_result.stderr or package_result.stdout

        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1

        deploy_result = _run_deploy_command(zip_files[0], target_dir, clean=True)

        assert deploy_result.returncode == 0, deploy_result.stderr or deploy_result.stdout
        assert (unrelated_dir / "keep-me.txt").read_text(encoding="utf-8") == "unchanged"


def test_deploy_helper_fails_clearly_when_zip_file_is_missing():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        missing_zip = tmp_path / "missing.zip"
        target_dir = tmp_path / "plugins" / "SmartBranching"
        target_dir.mkdir(parents=True)

        result = _run_deploy_command(missing_zip, target_dir)

        assert result.returncode != 0
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "zip" in combined_output.lower()
        assert "missing" in combined_output.lower() or "not found" in combined_output.lower()


def test_deploy_helper_fails_clearly_when_target_directory_is_missing():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        missing_target_dir = tmp_path / "plugins" / "SmartBranching"
        manifest_output = _copy_manifest_for_packaging(tmp_path)
        _write_fake_build_output(build_dir)

        package_result = _run_package_command(build_dir, output_dir, manifest_output=manifest_output)
        assert package_result.returncode == 0, package_result.stderr or package_result.stdout

        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1

        result = _run_deploy_command(zip_files[0], missing_target_dir)

        assert result.returncode != 0
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "target" in combined_output.lower()
        assert "directory" in combined_output.lower()
        assert "missing" in combined_output.lower() or "not found" in combined_output.lower()


def test_release_workflow_exists():
    workflow_path = _release_workflow_path()

    assert workflow_path.exists(), "Expected .github/workflows/release.yml to exist."


def test_release_workflow_contains_build_test_package_and_release_steps():
    workflow_path = _release_workflow_path()
    contents = workflow_path.read_text(encoding="utf-8")
    lowered = contents.lower()

    assert "dotnet build" in lowered
    assert "dotnet test" in lowered
    assert "package_plugin.py" in contents
    assert "manifest.json" in contents
    assert "upload" in lowered or "release" in lowered
