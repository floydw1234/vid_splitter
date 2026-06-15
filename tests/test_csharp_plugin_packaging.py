from pathlib import Path
import json
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


def _package_script_path() -> Path:
    return _repo_root() / "csharp_plugin" / "scripts" / "package_plugin.py"


def _write_fake_build_output(build_dir: Path, *, include_zstd: bool = True) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "Jellyfin.Plugin.SmartBranching.dll").write_bytes(b"fake-plugin-dll")
    (build_dir / "Jellyfin.Plugin.SmartBranching.deps.json").write_text(
        '{"runtimeTarget":{"name":"net8.0"}}',
        encoding="utf-8",
    )
    if include_zstd:
        (build_dir / "ZstdSharp.dll").write_bytes(b"fake-zstd-dll")


def _run_package_command(build_dir: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_package_script_path()),
            "--build-output",
            str(build_dir),
            "--output-dir",
            str(output_dir),
        ],
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
    manifest_path = _find_manifest_path()

    assert manifest_path is not None, (
        "Expected a repository manifest at repo root or csharp_plugin/manifest.json "
        "for a reproducible plugin package/install workflow."
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        _write_fake_build_output(build_dir)

        result = _run_package_command(build_dir, output_dir)

        assert result.returncode == 0, result.stderr or result.stdout
        zip_files = list(output_dir.glob("*.zip"))
        assert len(zip_files) == 1


def test_package_command_fails_when_declared_zstd_artifact_is_missing():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        build_dir = tmp_path / "build-output"
        output_dir = tmp_path / "dist"
        _write_fake_build_output(build_dir, include_zstd=False)

        result = _run_package_command(build_dir, output_dir)

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
        _write_fake_build_output(build_dir)
        build_yaml_path.write_text(mutated_text, encoding="utf-8")
        try:
            result = _run_package_command(build_dir, output_dir)
        finally:
            build_yaml_path.write_text(original_text, encoding="utf-8")

    assert result.returncode != 0
    combined_output = f"{result.stdout}\n{result.stderr}"
    assert "version" in combined_output.lower()
    assert "mismatch" in combined_output.lower()
