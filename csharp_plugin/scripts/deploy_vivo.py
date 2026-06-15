from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from zipfile import ZipFile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-zip", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def clear_target_directory(target_dir: Path) -> None:
    for child in target_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> int:
    args = parse_args()
    package_zip = Path(args.package_zip)
    target_dir = Path(args.target_dir)

    if not package_zip.exists():
        print(f"Package zip is missing: {package_zip}", file=sys.stderr)
        return 1
    if not package_zip.is_file():
        print(f"Package zip path is not a file: {package_zip}", file=sys.stderr)
        return 1

    if not target_dir.exists():
        print(f"Target directory is missing: {target_dir}", file=sys.stderr)
        return 1
    if not target_dir.is_dir():
        print(f"Target directory is invalid: {target_dir}", file=sys.stderr)
        return 1

    if args.clean:
        clear_target_directory(target_dir)

    with ZipFile(package_zip) as archive:
        archive.extractall(target_dir)

    print(f"Deployed {package_zip.name} to {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
