from pathlib import Path
import json
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
