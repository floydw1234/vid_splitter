from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_build_yaml(path: Path) -> dict[str, object]:
    data: dict[str, object] = {}
    artifacts: list[str] = []
    current_key: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
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


def read_props_version(path: Path) -> str:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    version = root.findtext(".//Version")
    if not version:
        raise ValueError(f"Missing Version in {path}")
    return version


def slugify_plugin_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def manifest_path(root: Path) -> Path:
    return root / "csharp_plugin" / "manifest.json"


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_manifest(root: Path, metadata: dict[str, object], zip_path: Path, checksum: str) -> None:
    path = manifest_path(root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    plugin = manifest[0]
    versions = plugin.setdefault("versions", [])

    version = str(metadata["version"])
    target_abi = str(metadata["targetAbi"])
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_url = f"https://example.invalid/{zip_path.name}"

    latest_entry = {
        "version": version,
        "targetAbi": target_abi,
        "sourceUrl": source_url,
        "checksum": checksum,
        "timestamp": timestamp,
    }

    if versions:
        versions[0] = latest_entry
    else:
        versions.append(latest_entry)

    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-output", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    build_yaml_path = root / "csharp_plugin" / "build.yaml"
    props_path = root / "csharp_plugin" / "Directory.Build.props"
    build_output = Path(args.build_output)
    output_dir = Path(args.output_dir)

    metadata = read_build_yaml(build_yaml_path)
    build_yaml_version = metadata.get("version")
    props_version = read_props_version(props_path)

    if build_yaml_version != props_version:
        print(
            f"Version mismatch: build.yaml has {build_yaml_version}, "
            f"Directory.Build.props has {props_version}.",
            file=sys.stderr,
        )
        return 1

    artifacts = metadata.get("artifacts", [])
    missing = [artifact for artifact in artifacts if not (build_output / artifact).exists()]
    if missing:
        print(
            "Missing declared artifacts in build output: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_name = str(metadata.get("name", "plugin"))
    version = str(build_yaml_version)
    zip_name = f"{slugify_plugin_name(plugin_name)}-{version}.zip"
    zip_path = output_dir / zip_name

    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            artifact_path = build_output / artifact
            archive.write(artifact_path, arcname=artifact)

    checksum = calculate_sha256(zip_path)
    update_manifest(root, metadata, zip_path, checksum)

    print(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
