"""Load avoided-topic lists for the analyzer.

The Jellyfin plugin owns per-user decisions. Authoring only needs the union of
topic names so it knows what to label (and, in swap mode, what to generate).
"""

from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

SMART_BRANCHING_PLUGIN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
HEADER_ALIASES = {"topic", "topics"}


def parse_topics_tsv(text: str) -> tuple[str, ...]:
    """Parse avoided topic names. One topic per line; a tab starts an optional note.

        nudity
        religion_christianity	crosses, churches, Jesus
    """
    topics: list[str] = []
    seen: set[str] = set()
    header_consumed = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = [part.strip() for part in line.split("\t")]
        if not cells or not cells[0]:
            continue
        first = cells[0].lower().replace(" ", "")
        if not header_consumed and first in HEADER_ALIASES:
            header_consumed = True
            continue
        header_consumed = True
        key = cells[0].strip()
        if key and key not in seen:
            seen.add(key)
            topics.append(key)
    return tuple(topics)


def parse_topics_tsv_file(path: str | Path) -> tuple[str, ...]:
    return parse_topics_tsv(Path(path).read_text())


def topics_from_jellyfin_config(payload: dict[str, Any]) -> tuple[str, ...]:
    """Extract the union of Profile.Topics from a plugin configuration JSON object."""
    entries = (
        payload.get("UserProfileEntries")
        or payload.get("userProfileEntries")
        or []
    )
    topics: list[str] = []
    seen: set[str] = set()

    def add(label: Any) -> None:
        if not isinstance(label, str):
            return
        key = label.strip()
        if key and key not in seen:
            seen.add(key)
            topics.append(key)

    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            profile = entry.get("Profile") or entry.get("profile") or {}
            if not isinstance(profile, dict):
                continue
            stored = profile.get("Topics") or profile.get("topics") or []
            if isinstance(stored, str):
                for part in stored.split(","):
                    add(part)
            elif isinstance(stored, list):
                for part in stored:
                    add(part)

    collected = payload.get("CollectedTopics") or payload.get("collectedTopics")
    if isinstance(collected, list):
        for part in collected:
            add(part)
    custom = payload.get("CustomTopics") or payload.get("customTopics") or []
    if isinstance(custom, str):
        for part in custom.split(","):
            add(part)
    elif isinstance(custom, list):
        for part in custom:
            add(part)
    return tuple(topics)


def topics_from_jellyfin_xml(path: str | Path) -> tuple[str, ...]:
    """Read CustomTopics and per-user Topics from the plugin XML on disk."""
    tree = ET.parse(path)
    topics: list[str] = []
    seen: set[str] = set()
    for element in tree.getroot().findall(".//Topic"):
        key = (element.text or "").strip()
        if key and key not in seen:
            seen.add(key)
            topics.append(key)
    return tuple(topics)


def default_jellyfin_plugin_config() -> Path | None:
    """Plugin XML written by Jellyfin, if present on this machine."""
    env = (os.environ.get("JELLYFIN_PLUGIN_CONFIG") or "").strip()
    if env:
        path = Path(env).expanduser()
        return path if path.is_file() else None
    candidates: list[Path] = []
    plugin_dir = (os.environ.get("JELLYFIN_PLUGIN_DIR") or "").strip()
    if plugin_dir:
        candidates.append(
            Path(plugin_dir).expanduser() / "configurations" / "Jellyfin.Plugin.SmartBranching.xml"
        )
    candidates.append(
        Path.home()
        / "servers"
        / "jellyfin"
        / "config"
        / "plugins"
        / "configurations"
        / "Jellyfin.Plugin.SmartBranching.xml"
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def topics_from_jellyfin_config_file(path: str | Path) -> tuple[str, ...]:
    config_path = Path(path)
    suffix = config_path.suffix.lower()
    if suffix == ".xml":
        return topics_from_jellyfin_xml(config_path)
    payload = json.loads(config_path.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError("Jellyfin plugin config was not a JSON object")
    return topics_from_jellyfin_config(payload)


def fetch_jellyfin_plugin_config(
    base_url: str,
    api_key: str,
    plugin_id: str = SMART_BRANCHING_PLUGIN_ID,
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/Plugins/{plugin_id}/Configuration"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f'MediaBrowser Token="{api_key}"',
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"Jellyfin plugin config request failed ({exc.code}): {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Jellyfin plugin config request failed: {exc.reason}") from exc
    if not raw:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Jellyfin plugin config was not a JSON object")
    return payload


def collect_filter_topics(
    *,
    topics_file: str | Path | None = None,
    jellyfin_url: str | None = None,
    jellyfin_api_key: str | None = None,
    jellyfin_config: str | Path | None = None,
    extra: tuple[str, ...] | list[str] = (),
    plugin_id: str = SMART_BRANCHING_PLUGIN_ID,
) -> tuple[str, ...]:
    """Union topics from a TSV, Jellyfin plugin config, and an explicit extra list."""
    combined: list[str] = []
    seen: set[str] = set()

    def extend(labels: tuple[str, ...] | list[str]) -> None:
        for label in labels:
            key = str(label).strip()
            if key and key not in seen:
                seen.add(key)
                combined.append(key)

    if topics_file:
        extend(parse_topics_tsv_file(topics_file))
    if jellyfin_config:
        extend(topics_from_jellyfin_config_file(jellyfin_config))
    url = (jellyfin_url or os.environ.get("JELLYFIN_BASE_URL") or "").strip()
    api_key = (jellyfin_api_key or os.environ.get("JELLYFIN_API_KEY") or "").strip()
    if url and api_key:
        payload = fetch_jellyfin_plugin_config(url, api_key, plugin_id=plugin_id)
        extend(topics_from_jellyfin_config(payload))
    elif url or api_key:
        logger.warning("Jellyfin topic pull needs both --jellyfin-url and --jellyfin-api-key (or env)")
    extend(extra)
    return tuple(combined)
