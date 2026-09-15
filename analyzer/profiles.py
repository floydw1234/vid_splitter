"""Load extra BVF profile definitions from JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_profile_file(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load one or more profile definitions.

    Accepts either a single profile object::

        {"name": "strict_parent", "filters": {"religion_christianity": "skip"}}

    or a mapping::

        {"profiles": {"strict_parent": {"filters": {...}}}}
    """
    source = Path(path)
    payload = json.loads(source.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Profile file {source} must contain a JSON object")

    if isinstance(payload.get("profiles"), dict):
        loaded: dict[str, dict[str, Any]] = {}
        for key, value in payload["profiles"].items():
            loaded[str(key)] = _normalize_profile(str(key), value)
        if not loaded:
            raise ValueError(f"Profile file {source} has an empty profiles object")
        return loaded

    key = str(payload.get("id") or payload.get("key") or source.stem)
    return {key: _normalize_profile(key, payload)}


def load_profile_files(paths: list[str | Path]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        merged.update(load_profile_file(path))
    return merged


def _normalize_profile(key: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Profile {key!r} must be an object")
    filters = value.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError(f"Profile {key!r} filters must be an object")
    return {
        "name": str(value.get("name") or key),
        "description": str(value.get("description") or ""),
        "filters": {str(tag): str(action) for tag, action in filters.items()},
    }
