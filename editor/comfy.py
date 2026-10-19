"""Minimal ComfyUI HTTP client (no process management)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class ComfyClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("COMFYUI_URL") or "http://127.0.0.1:8188").rstrip(
            "/"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120.0,
    ) -> bytes:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        if headers:
            for key, val in headers.items():
                req.add_header(key, val)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            raise ConnectionError(f"ComfyUI not reachable at {self.base_url}: {exc}") from exc

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        raw = self._request(method, path, data=data, headers=headers)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def queue_status(self) -> dict[str, Any]:
        return self._json("GET", "/queue")

    def upload_file(self, path: Path, *, overwrite: bool = True) -> str:
        """Upload image or video; returns the name ComfyUI stored."""
        path = path.resolve()
        with path.open("rb") as fh:
            body = fh.read()
        filename = path.name
        # multipart/form-data
        boundary = "----vidSplitterComfyBoundary"
        parts: list[bytes] = []
        for key, val in (("overwrite", "true" if overwrite else "false"),):
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            parts.append(f"{val}\r\n".encode())
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode()
        )
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(body)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        payload = b"".join(parts)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        raw = self._request("POST", "/upload/image", data=payload, headers=headers, timeout=600.0)
        resp = json.loads(raw.decode("utf-8"))
        name = resp.get("name") or filename
        return str(name)

    def submit(self, graph: dict[str, Any]) -> str:
        resp = self._json("POST", "/prompt", {"prompt": graph})
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI did not queue the prompt: {resp}")
        return str(prompt_id)

    def history(self, prompt_id: str) -> dict[str, Any]:
        hist = self._json("GET", f"/history/{prompt_id}")
        if isinstance(hist, dict) and prompt_id in hist:
            return hist[prompt_id]
        if isinstance(hist, dict):
            return hist
        return {}

    def free(self) -> None:
        self._json("POST", "/free", {"unload_models": True, "free_memory": True})

    def download_view(
        self,
        filename: str,
        *,
        subfolder: str = "",
        folder_type: str = "output",
    ) -> bytes:
        q = urllib.parse.urlencode(
            {
                "filename": filename,
                "subfolder": subfolder,
                "type": folder_type,
            }
        )
        return self._request("GET", f"/view?{q}", timeout=600.0)


def queue_is_idle(status: dict[str, Any]) -> bool:
    running = status.get("queue_running") or []
    pending = status.get("queue_pending") or []
    return not running and not pending


def prompt_queue_position(status: dict[str, Any], prompt_id: str) -> int | None:
    """Return 1-based position in pending queue, or 0 if running, or None if absent."""
    for item in status.get("queue_running") or []:
        if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id:
            return 0
    for i, item in enumerate(status.get("queue_pending") or []):
        if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id:
            return i + 1
    return None


def _output_media_from_node(node_out: Any) -> tuple[str, str, str] | None:
    if not isinstance(node_out, dict):
        return None
    for key in ("gifs", "videos", "images"):
        items = node_out.get(key)
        if not items:
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("filename"):
                continue
            folder_type = str(item.get("type") or "output")
            if folder_type != "output":
                continue
            return (
                str(item["filename"]),
                str(item.get("subfolder") or ""),
                folder_type,
            )
    return None


def find_output_media(
    outputs: dict[str, Any],
    *,
    prefer_node: str | None = None,
) -> tuple[str, str, str] | None:
    """Return (filename, subfolder, folder_type) from history outputs."""
    if prefer_node and prefer_node in outputs:
        found = _output_media_from_node(outputs[prefer_node])
        if found:
            return found
    for node_id, node_out in outputs.items():
        if prefer_node and str(node_id) == prefer_node:
            continue
        found = _output_media_from_node(node_out)
        if found:
            return found
    return None
