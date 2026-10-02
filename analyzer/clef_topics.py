"""Cloudflare clef-flash visual avoid-topic detection (full-frame R3 schema).

Lazy-loads the joint-schema release model on first scan. Importing this module
does not pull in torch or transformers.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_CLEF_HF_REPO = "Cloudflare/clef-flash"
DEFAULT_CLEF_MANUAL_SNAPSHOT = (
    Path.home() / ".cache/clef-flash-manual/17f0b0ad64efb65d273590632833508766b2aae6"
)
DEFAULT_CLEF_THRESHOLD = 0.5
DEFAULT_CLEF_SCAN_INTERVAL = 2.0

FULL_STATE = (
    "A single frame from a children's educational video (Ms Rachel style). Decide which avoid-topics "
    "are visibly present in this exact frame. People in small picture-in-picture insets count. "
    "Be conservative: when unsure, answer false."
)

R3_Q = {
    "asian_people": {"type": "noul",
        "instructions": "Is at least one person with East, South, or Southeast Asian appearance visibly present in this frame (including children and small insets)?",
        "criteria": {"true": "A visibly present person clearly has East/South/Southeast Asian appearance cues.",
                     "false": "No visibly present person clearly has Asian appearance cues (androgynous presenters are not Asian by default)."}},
    "black_people": {"type": "noul",
        "instructions": "Is at least one person with Black / African-diaspora appearance visibly present in this frame (including children and small insets)?",
        "criteria": {"true": "A visibly present person has Black/African-diaspora appearance cues (medium/tan or darker skin with tight curly hair counts when clearly present).",
                     "false": "No visibly present person has Black/African-diaspora appearance cues."}},
    "lgbtq": {"type": "noul",
        "instructions": "Is there an explicit pride/LGBTQ flag, or an adult presenter with clear LGBTQ / androgynous presentation cues (e.g. beanie, facial piercing, androgynous look, like the presenter Jules), in this frame?",
        "criteria": {"true": "An explicit pride/LGBTQ flag or a clearly LGBTQ/androgynous-presenting adult presenter is visible.",
                     "false": "No such cue. Rainbow toys, rainbow clothing colors, or rainbow set dressing alone do not count."}},
}

CLEF_QUESTION_IDS = ("asian_people", "black_people", "lgbtq")

# Normalized taxonomy keys that route to clef (mirrors APPEARANCE_LABELS + lgbtq).
_NORMALIZED_LABEL_TO_CLEF_QID: dict[str, str] = {
    "asian people": "asian_people",
    "asian person": "asian_people",
    "black people": "black_people",
    "lgbtq": "lgbtq",
}

_DEFAULT_ACTIVE_LABELS = frozenset(
    {"asian people", "black people", "lgbtq"},
)

CLEF_COVERED_LABELS = frozenset(_NORMALIZED_LABEL_TO_CLEF_QID.keys())


def normalized_visual_label(label: str) -> str:
    return str(label).strip().lower()


def clef_question_id_for_label(label: str) -> str | None:
    return _NORMALIZED_LABEL_TO_CLEF_QID.get(normalized_visual_label(label))


def taxonomy_keys_for_clef_question(
    taxonomy_keys: set[str] | frozenset[str],
    question_id: str,
) -> list[str]:
    return sorted(
        key
        for key in taxonomy_keys
        if clef_question_id_for_label(key) == question_id
    )


def default_clef_model_path() -> str:
    env_path = os.environ.get("VID_SPLITTER_CLEF_MODEL", "").strip()
    if env_path:
        return env_path
    if DEFAULT_CLEF_MANUAL_SNAPSHOT.is_dir():
        return str(DEFAULT_CLEF_MANUAL_SNAPSHOT)
    return DEFAULT_CLEF_HF_REPO


def resolve_clef_snapshot_dir(model_path: str | None) -> str:
    """Return a local directory containing joint_schema_model.py."""
    raw = (model_path or "").strip() or default_clef_model_path()
    path = Path(raw).expanduser()
    if path.is_dir():
        return str(path.resolve())
    from huggingface_hub import snapshot_download

    return snapshot_download(raw)


def partition_visual_labels(taxonomy: dict[str, str]) -> tuple[set[str], set[str]]:
    """Split taxonomy keys into clef-flash vs CLIP fallback buckets."""
    keys = set(taxonomy.keys())
    clef = {key for key in keys if clef_question_id_for_label(key) is not None}
    clip = keys - clef
    return clef, clip


def log_visual_label_routing(
    *,
    backend: str,
    clef_labels: set[str],
    clip_labels: set[str],
    skipped_labels: set[str],
) -> None:
    logger.info(
        "Visual topics (%s): clef-flash [%s]; CLIP fallback [%s]; skipped [%s]",
        backend,
        ", ".join(sorted(clef_labels)) or "none",
        ", ".join(sorted(clip_labels)) or "none",
        ", ".join(sorted(skipped_labels)) or "none",
    )


class ClefFlashTopicScanner:
    """Full-frame clef-flash R3 avoid-topic scanner."""

    def __init__(
        self,
        model_path: str | None = None,
        threshold: float = DEFAULT_CLEF_THRESHOLD,
        device: str = "cuda",
        *,
        active_labels: set[str] | frozenset[str] | None = None,
    ):
        self.model_path = model_path
        self.threshold = float(threshold)
        self._requested_device = str(device)
        self.device = str(device)
        self.active_labels = frozenset(active_labels or _DEFAULT_ACTIVE_LABELS)
        self._model = None
        self._processor = None
        self._snapshot_dir: str | None = None
        self.last_scores: dict[str, float] = {}

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        import torch

        if self._requested_device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA unavailable for clef-flash; falling back to CPU")
            self.device = "cpu"
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        snapshot = resolve_clef_snapshot_dir(self.model_path)
        self._snapshot_dir = snapshot
        if snapshot not in sys.path:
            sys.path.insert(0, snapshot)
        from joint_schema_model import collate_records, encode_record, load_release_model

        self._collate_records = collate_records
        self._encode_record = encode_record
        logger.info("Loading clef-flash for visual topics from %s on %s", snapshot, self.device)
        model, processor = load_release_model(snapshot, device=self.device, dtype=dtype)
        model.eval()
        self._model = model
        self._processor = processor
        self._torch = torch

    def scores_for_image(self, image: Image.Image) -> dict[str, float]:
        self._ensure_model()
        image = image.convert("RGB")
        question_ids = [
            qid
            for qid in CLEF_QUESTION_IDS
            if taxonomy_keys_for_clef_question(self.active_labels, qid)
        ]
        if not question_ids:
            self.last_scores = {}
            return {}
        questions = {qid: R3_Q[qid] for qid in question_ids}
        rec = {"state": FULL_STATE, "images": [image], "questions": questions}
        enc = self._encode_record(self._processor.tokenizer, rec, processor=self._processor)
        batch = self._collate_records(
            [enc],
            self._processor.tokenizer.pad_token_id,
            self._torch.device(self.device),
        )
        with self._torch.inference_mode():
            logits = self._model(batch)[0]
        scores: dict[str, float] = {}
        for question, row_logits in zip(enc.questions, logits):
            qid = question.question_id
            keys = taxonomy_keys_for_clef_question(self.active_labels, qid)
            if not keys:
                continue
            probs = row_logits.float().softmax(-1)
            true_index = list(question.option_ids).index("true")
            prob = float(probs[true_index])
            for key in keys:
                scores[key] = prob
        self.last_scores = dict(scores)
        for label, prob in sorted(scores.items()):
            logger.debug("clef-flash %s p_true=%.4f", label, prob)
        return scores

    def topics_for_image(self, image: Image.Image) -> list[str]:
        scores = self.scores_for_image(image)
        return sorted(key for key, prob in scores.items() if prob >= self.threshold)

    def topics_for_path(self, path: str | Path) -> list[str]:
        with Image.open(path) as image:
            return self.topics_for_image(image)


class HybridVisualTopicScanner:
    """Clef for appearance/LGBTQ cues; CLIP for remaining visual taxonomy labels."""

    def __init__(
        self,
        *,
        clef: ClefFlashTopicScanner | None,
        clip: Any | None,
        clef_labels: set[str],
        clip_labels: set[str],
        skipped_labels: set[str],
        backend: str = "clef-flash",
    ):
        self._clef = clef
        self._clip = clip
        self.clef_labels = set(clef_labels)
        self.clip_labels = set(clip_labels)
        self.skipped_labels = set(skipped_labels)
        self.backend = backend
        log_visual_label_routing(
            backend=backend,
            clef_labels=self.clef_labels,
            clip_labels=self.clip_labels if clip is not None else set(),
            skipped_labels=self.skipped_labels | (
                self.clip_labels if clip is None and self.clip_labels else set()
            ),
        )

    def topics_for_image(self, image: Image.Image) -> list[str]:
        hits: set[str] = set()
        if self._clef is not None:
            hits.update(self._clef.topics_for_image(image))
        if self._clip is not None:
            clip_hits = self._clip.topics_for_image(image)
            hits.update(topic for topic in clip_hits if topic in self.clip_labels)
        return sorted(hits)

    def topics_for_path(self, path: str | Path) -> list[str]:
        with Image.open(path) as image:
            return self.topics_for_image(image)


def build_hybrid_visual_scanner(
    *,
    taxonomy: dict[str, str],
    device: str,
    clef_model_path: str | None,
    clef_threshold: float,
    clip_threshold: float,
    clip_gated_inset_black: bool,
    clip_factory: Any,
) -> HybridVisualTopicScanner:
    """Create a hybrid scanner; clip_factory(taxonomy_subset) -> ClipTopicScanner."""
    clef_labels, clip_labels = partition_visual_labels(taxonomy)
    skipped: set[str] = set()
    clef_scanner: ClefFlashTopicScanner | None = None
    if clef_labels:
        clef_scanner = ClefFlashTopicScanner(
            model_path=clef_model_path,
            threshold=clef_threshold,
            device=device,
            active_labels=clef_labels,
        )
    clip_scanner = None
    if clip_labels:
        try:
            clip_taxonomy = {key: taxonomy[key] for key in clip_labels}
            clip_scanner = clip_factory(clip_taxonomy)
        except Exception as exc:
            logger.warning("CLIP fallback scanner unavailable: %s", exc)
            skipped = set(clip_labels)
    return HybridVisualTopicScanner(
        clef=clef_scanner,
        clip=clip_scanner,
        clef_labels=clef_labels,
        clip_labels=clip_labels if clip_scanner is not None else set(),
        skipped_labels=skipped,
        backend="clef-flash",
    )
