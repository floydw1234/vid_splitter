"""Open-vocab boxes from NVIDIA LocateAnything via locate-anything.cpp.

Evaluation / bakeoff only — production analyze uses CLIP + Faster R-CNN person
crops (`analyzer/visual_topics.py`). Ms Rachel bakeoff (2026-09-03) showed
combined and per-tag LocateAnything hurt precision on appearance and lgbtq tags.
See `tools/vision_bakeoff.py` and `output/vision_bakeoff/`.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_LIB = Path(
    os.environ.get(
        "LOCATE_ANYTHING_LIB",
        "/home/william/Documents/codingProj/locate-anything.cpp/build/liblocate_anything.so",
    )
)
DEFAULT_MODEL = Path(
    os.environ.get(
        "LOCATE_ANYTHING_MODEL",
        "/home/william/Documents/codingProj/locate-anything.cpp/models/locate-anything-q8_0.gguf",
    )
)

# Prompt text is what the model returns as `label`. Visual tags only;
# spoken-only classes (profanity, racism, etc.) stay on MiniLM.
# A topic may list several detect phrases; any hit maps back to that tag.
DEFAULT_PROMPTS = {
    "black people": "Black person",
    "asian people": "East Asian person",
    "lgbtq": "rainbow pride flag",
    "lesbians": [
        "two women kissing",
        "butch lesbian woman",
    ],
    "nudity": "nude person with exposed breasts or genitals",
    "religion_christianity": "Christian cross or crucifix",
    "religion_general": "person praying at a temple or mosque",
    "politics": "political campaign sign or protest sign",
    "drugs": "illegal drugs or a syringe",
    "alcohol": "beer bottle or wine glass",
    "smoking": "lit cigarette",
    "war_military": "soldier in military uniform",
    "violence_domestic": "person punching another person",
    "gore": "bloody open wound",
}

LocateFn = Callable[[str, str, int], list[dict[str, Any]]]
PromptMap = dict[str, str | list[str]]


def prompt_phrases(prompt: str | list[str]) -> list[str]:
    if isinstance(prompt, str):
        return [prompt] if prompt.strip() else []
    return [part for part in prompt if str(part).strip()]


def locate_prompt_for_phrase(phrase: str) -> str:
    return (
        "Locate all the instances that matches the following description: "
        f"{phrase.strip()}."
    )


def locate_prompt(prompts: PromptMap) -> str:
    descriptions: list[str] = []
    for value in prompts.values():
        descriptions.extend(prompt_phrases(value))
    return (
        "Locate all the instances that matches the following description: "
        f"{'</c>'.join(descriptions)}."
    )


def labels_to_topics(
    detections: list[dict[str, Any]],
    prompts: PromptMap,
) -> list[str]:
    """Map LocateAnything labels back onto analyzer topic ids."""
    hits: set[str] = set()
    for det in detections:
        raw = str(det.get("label") or "").strip().lower()
        if not raw:
            continue
        for topic, prompt in prompts.items():
            for needle in prompt_phrases(prompt):
                needle = needle.strip().lower()
                if raw == needle or needle in raw or raw in needle:
                    hits.add(topic)
                    break
    return sorted(hits)


def merge_visual_labels(
    clip_hits: list[str],
    locate_hits: list[str],
    locate_keys: set[str] | None = None,
) -> list[str]:
    """LocateAnything owns keys it was asked about; CLIP keeps the rest.

    CLIP-only hits on a Locate key are dropped (false-positive cleanup).
    Locate-only hits are kept (false-negative cleanup).
    """
    clip_set = set(clip_hits)
    loc_set = set(locate_hits)
    owned = set(locate_keys) if locate_keys is not None else loc_set
    kept = (clip_set - owned) | loc_set
    return sorted(kept)


class LocateAnythingScanner:
    def __init__(
        self,
        lib_path: str | Path = DEFAULT_LIB,
        model_path: str | Path = DEFAULT_MODEL,
        prompts: PromptMap | None = None,
        threads: int = 0,
        mode: int = 0,
        locate: LocateFn | None = None,
    ):
        self.lib_path = Path(lib_path)
        self.model_path = Path(model_path)
        self.prompts = dict(prompts or DEFAULT_PROMPTS)
        self.threads = int(threads)
        self.mode = int(mode)
        self._locate = locate
        self._lib = None
        self._ctx = None

    def available(self) -> bool:
        if self._locate is not None:
            return True
        return self.lib_path.is_file() and self.model_path.is_file()

    def close(self) -> None:
        if self._lib is not None and self._ctx is not None:
            self._lib.la_capi_free(self._ctx)
        self._ctx = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _ensure_engine(self) -> None:
        if self._locate is not None or self._ctx is not None:
            return
        if not self.lib_path.is_file():
            raise FileNotFoundError(self.lib_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)
        lib = ctypes.CDLL(str(self.lib_path))
        lib.la_capi_load.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.la_capi_load.restype = ctypes.c_void_p
        lib.la_capi_free.argtypes = [ctypes.c_void_p]
        lib.la_capi_free.restype = None
        lib.la_capi_locate_path.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.la_capi_locate_path.restype = ctypes.c_void_p
        lib.la_capi_free_string.argtypes = [ctypes.c_void_p]
        lib.la_capi_free_string.restype = None
        lib.la_capi_last_error.argtypes = [ctypes.c_void_p]
        lib.la_capi_last_error.restype = ctypes.c_char_p
        ctx = lib.la_capi_load(str(self.model_path).encode(), self.threads)
        if not ctx:
            raise RuntimeError(f"LocateAnything failed to load {self.model_path}")
        self._lib = lib
        self._ctx = ctx
        logger.info("Loaded LocateAnything %s", self.model_path)

    def _locate_with_prompt(self, path: str | Path, prompt: str) -> list[dict[str, Any]]:
        if self._locate is not None:
            return list(self._locate(str(path), prompt, self.mode))
        self._ensure_engine()
        assert self._lib is not None and self._ctx is not None
        raw = self._lib.la_capi_locate_path(
            self._ctx,
            str(path).encode(),
            prompt.encode(),
            self.mode,
        )
        if not raw:
            err = self._lib.la_capi_last_error(self._ctx) or b""
            raise RuntimeError(err.decode() or "LocateAnything locate failed")
        try:
            payload = ctypes.cast(raw, ctypes.c_char_p).value or b"{}"
            parsed = json.loads(payload.decode())
        finally:
            self._lib.la_capi_free_string(raw)
        return list(parsed.get("detections") or [])

    def detections_for_path(
        self,
        path: str | Path,
        *,
        per_topic: bool = False,
    ) -> list[dict[str, Any]]:
        if not per_topic:
            return self._locate_with_prompt(path, locate_prompt(self.prompts))
        merged: list[dict[str, Any]] = []
        for topic, prompt in self.prompts.items():
            for phrase in prompt_phrases(prompt):
                for det in self._locate_with_prompt(path, locate_prompt_for_phrase(phrase)):
                    row = dict(det)
                    row["topic"] = topic
                    row["phrase"] = phrase
                    merged.append(row)
        return merged

    def topics_for_path(self, path: str | Path, *, per_topic: bool = False) -> list[str]:
        if not per_topic:
            return labels_to_topics(self.detections_for_path(path), self.prompts)
        hits: set[str] = set()
        for topic, prompt in self.prompts.items():
            for phrase in prompt_phrases(prompt):
                if self._locate_with_prompt(path, locate_prompt_for_phrase(phrase)):
                    hits.add(topic)
                    break
        return sorted(hits)

    def _legacy_detections_for_path(self, path: str | Path) -> list[dict[str, Any]]:
        """Combined multi-class prompt (legacy; crowded prompts miss detections)."""
        prompt = locate_prompt(self.prompts)
        if self._locate is not None:
            return list(self._locate(str(path), prompt, self.mode))
        return self._locate_with_prompt(path, prompt)

    def topics_for_path_combined_prompt(self, path: str | Path) -> list[str]:
        return labels_to_topics(self._legacy_detections_for_path(path), self.prompts)


class CombinedVisualScanner:
    """CLIP first, then LocateAnything corrects the labels it owns.

    Not used in production analyze — bakeoff showed this merge drops CLIP lgbtq
    hits and adds appearance false positives. Kept for eval tooling only.
    """

    def __init__(self, clip, locate: LocateAnythingScanner):
        self.clip = clip
        self.locate = locate

    def topics_for_path(self, path: str | Path) -> list[str]:
        clip_hits = list(self.clip.topics_for_path(path))
        try:
            locate_hits = self.locate.topics_for_path(path, per_topic=True)
        except Exception as exc:
            logger.warning("LocateAnything failed on %s: %s", path, exc)
            return clip_hits
        return merge_visual_labels(
            clip_hits,
            locate_hits,
            locate_keys=set(self.locate.prompts),
        )
