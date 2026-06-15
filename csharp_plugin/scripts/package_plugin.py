from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET


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

    print(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
