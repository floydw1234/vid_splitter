"""Visual topic labels for analyzer buckets (production vision stack).

Topic ID is CLIP, not SAM2. SAM2 only masks regions later for Wan Animate.
Transcript LLM covers spoken topics. Appearance labels (Black / East Asian
people) are scored on Faster R-CNN person crops so a white presenter does not
drown out a child in their arms. Tiny satellite crops (split-screen insets,
duplicate presenters) are dropped from the primary pass so they do not steal
the appearance label; a second gated inset pass can still add ``black people``
for small insets that the primary filter would miss (Ms Rachel bakeoff).

Qwen2.5-VL / other VLMs were evaluated offline only and are not wired into
this production path. Bakeoff winner: CLIP + Faster R-CNN person crops with
gated black insets — see output/vision_bakeoff/.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from analyzer.topic_classifier import TOPIC_TAXONOMY

logger = logging.getLogger(__name__)

NONE_PROMPT = "a photo of an ordinary scene with no notable symbols or mature content"
SCENE_NONE_PROMPT = "a photo of people talking, singing, or standing with no romantic couple"
SCENE_CLASS_PROMPTS = {
    "lesbians": [
        "a photo of two women kissing as a romantic lesbian couple",
        "a photo of a parent holding a baby",
        "a photo of people singing or talking",
    ],
}

VISUAL_PROMPTS = {
    "nudity": "a photo of nude people, naked body, exposed breasts or genitals",
    "religion_christianity": (
        "a photo of a Christian cross, crucifix, church altar, Jesus, priest, Bible"
    ),
    "religion_general": "a photo of religious worship, prayer, temple, mosque, synagogue",
    "politics": "a photo of a political rally, campaign signs, government building, protest",
    "drugs": "a photo of illegal drugs, needles, smoking narcotics",
    "alcohol": "a photo of people drinking alcohol, beer bottles, a bar",
    "smoking": "a photo of a lit cigarette in someone's mouth with visible smoke",
    "lgbtq": "a photograph of a rainbow LGBTQ pride flag",
    "war_military": "a photo of soldiers, warfare, military combat",
    "violence_domestic": "a photograph of a person punching or beating another person",
    "gore": "a photo of bloody open wounds and graphic mutilation",
}

# Score these in their own competing softmax; index 0 of a group must win.
# lgbtq has two groups: pride flags, and this clip's beanie/piercing presenter.
# Orientation is not a visual class; the second group is a presentation proxy.
STRICT_VISUAL_PROMPTS = {
    "lgbtq": [
        [
            "a photograph of a rainbow LGBTQ pride flag",
            "a colorful children's TV set with bright toys and props",
            "a children's stacking toy with colorful plastic rings",
            "a cartoon illustration of a smiling star",
            "a children's presenter talking on camera",
        ],
        [
            "an androgynous person wearing a beanie and lip piercing",
            "Ms Rachel style presenter in overalls talking to camera",
            "a children's presenter on all fours pretending to be a dog",
            "a parent crawling on the floor playing pretend animals with kids",
            "a toddler or preschool child",
            "2D cartoon animation",
        ],
    ],
    "violence_domestic": [
        [
            "a photograph of a person punching or beating another person",
            "a parent holding a baby and shushing",
            "a person singing or talking with an animated face",
        ],
    ],
}

EXTRA_VISUAL_PROMPTS = {
    "black people": "a photo of a Black person with dark brown skin",
    "asian people": "a photo of an East Asian person",
    "asian person": "a photo of an East Asian person",
    "lesbians": "a photo of two women kissing as a romantic lesbian couple",
}

APPEARANCE_LABELS = {
    "black people": "black",
    "asian people": "asian",
    "asian person": "asian",
}

APPEARANCE_ADULT_PROMPTS = {
    "black": "a photo of a Black person",
    "asian": "a photo of an East Asian adult",
    "other": "a photo of a white person",
}

APPEARANCE_CHILD_PROMPTS = {
    "black": "a photo of a Black baby or Black child with dark brown skin",
    "asian": "a photo of an East Asian child",
    "other": "a photo of a white child",
}

DEFAULT_CLIP_MODEL = "openai/clip-vit-base-patch32"
DEFAULT_VISUAL_THRESHOLD = 0.45
# Appearance tags need denser sampling than the NSFW broad pass (default 5s).
DEFAULT_VISUAL_SCAN_INTERVAL = 1.0
DEFAULT_PERSON_SCORE = 0.55
DEFAULT_APPEARANCE_THRESHOLD = 0.50
DEFAULT_EXTRA_THRESHOLD = 0.70
# Drop person boxes smaller than this fraction of the largest box in the frame.
DEFAULT_MIN_PERSON_AREA_RATIO = 0.50
# Gated inset pass: allow smaller crops to contribute black people only.
DEFAULT_GATED_INSET_BLACK_ENABLED = True
DEFAULT_GATED_INSET_MIN_AREA_RATIO = 0.10
# Only score child appearance prompts on smaller person crops (not the presenter).
APPEARANCE_CHILD_MAX_AREA_RATIO = 0.65

# Strict softmax groups scored on person crops instead of the full frame.
# lgbtq group 0 (pride flag) stays full-frame; group 1 (presenter proxy) needs crops.
PERSON_CROP_STRICT_GROUP_INDEXES: dict[str, set[int]] = {
    "lgbtq": {1},
}
# Person-crop strict hits need a higher index-0 score than full-frame groups.
STRICT_PERSON_CROP_MIN_PROB: dict[str, float] = {
    "lgbtq": 0.97,
}
# Full-frame strict groups also need a confident index-0 win (weak ties are often toys/set dressing).
STRICT_FULL_FRAME_MIN_PROB: dict[str, float] = {
    "lgbtq": 0.50,
}
# Ignore tiny partial boxes; they often tag a limb while someone is crawling.
MIN_PERSON_CROP_AREA_RATIO: dict[str, float] = {
    "lgbtq": 0.18,
}

PersonBoxes = Callable[[Image.Image], list[tuple[float, float, float, float]]]


def strict_visual_groups(
    prompts: dict[str, list] | None = None,
) -> dict[str, list[list[str]]]:
    """Normalize STRICT_VISUAL_PROMPTS to one or more competing text groups."""
    source = STRICT_VISUAL_PROMPTS if prompts is None else prompts
    groups: dict[str, list[list[str]]] = {}
    for key, value in source.items():
        if not value:
            continue
        if isinstance(value[0], str):
            groups[key] = [list(value)]
        else:
            groups[key] = [list(group) for group in value]
    return groups


def keep_primary_person_crops(
    crops_with_area: list[tuple[Image.Image, float]],
    min_ratio: float = DEFAULT_MIN_PERSON_AREA_RATIO,
) -> list[tuple[Image.Image, float]]:
    """Keep the largest person crop and any others at least min_ratio as large."""
    if not crops_with_area:
        return []
    max_area = max(area for _crop, area in crops_with_area)
    return [
        (crop, area)
        for crop, area in crops_with_area
        if area >= min_ratio * max_area
    ]


def gated_inset_person_crops(
    crops_with_area: list[tuple[Image.Image, float]],
    *,
    primary_min_ratio: float = DEFAULT_MIN_PERSON_AREA_RATIO,
    inset_min_ratio: float = DEFAULT_GATED_INSET_MIN_AREA_RATIO,
) -> list[tuple[Image.Image, float]]:
    """Return inset crops too small for primary scoring but eligible for gated black."""
    if not crops_with_area:
        return []
    max_area = max(area for _crop, area in crops_with_area)
    primary_cutoff = primary_min_ratio * max_area
    inset_cutoff = inset_min_ratio * max_area
    return [
        (crop, area)
        for crop, area in crops_with_area
        if inset_cutoff <= area < primary_cutoff
    ]


def extra_visual_prompt(label: str) -> str:
    key = str(label).strip()
    if key in EXTRA_VISUAL_PROMPTS:
        return EXTRA_VISUAL_PROMPTS[key]
    return f"a photo of {key.replace('_', ' ')}"


def appearance_class_for_label(label: str) -> str | None:
    return APPEARANCE_LABELS.get(str(label).strip().lower())


def clip_label_groups(
    taxonomy: dict[str, str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split built-in CLIP prompts from author/Jellyfin extras."""
    builtin_keys = [
        key
        for key in VISUAL_PROMPTS
        if key in taxonomy and key not in STRICT_VISUAL_PROMPTS
    ]
    builtin_prompts = [VISUAL_PROMPTS[key] for key in builtin_keys]
    extra_keys = [key for key in taxonomy if key not in VISUAL_PROMPTS]
    extra_prompts = [extra_visual_prompt(key) for key in extra_keys]
    return builtin_keys, builtin_prompts, extra_keys, extra_prompts


def crop_windows(image: Image.Image) -> list[Image.Image]:
    """Full frame plus quadrants and center, for small off-center objects."""
    image = image.convert("RGB")
    width, height = image.size
    mid_x, mid_y = width // 2, height // 2
    boxes = [
        (0, 0, width, height),
        (0, 0, mid_x, mid_y),
        (mid_x, 0, width, mid_y),
        (0, mid_y, mid_x, height),
        (mid_x, mid_y, width, height),
        (width // 4, height // 4, width * 3 // 4, height * 3 // 4),
    ]
    return [image.crop(box) for box in boxes]


class ClipTopicScanner:
    def __init__(
        self,
        device: str = "cpu",
        threshold: float = DEFAULT_VISUAL_THRESHOLD,
        model_name: str = DEFAULT_CLIP_MODEL,
        taxonomy: dict[str, str] | None = None,
        person_boxes: PersonBoxes | None = None,
        person_score: float = DEFAULT_PERSON_SCORE,
        appearance_threshold: float = DEFAULT_APPEARANCE_THRESHOLD,
        extra_threshold: float = DEFAULT_EXTRA_THRESHOLD,
        min_person_area_ratio: float = DEFAULT_MIN_PERSON_AREA_RATIO,
        gated_inset_black: bool = DEFAULT_GATED_INSET_BLACK_ENABLED,
        gated_inset_min_area_ratio: float = DEFAULT_GATED_INSET_MIN_AREA_RATIO,
    ):
        self.device = device
        self.threshold = float(threshold)
        self.model_name = model_name
        self.taxonomy = taxonomy or {**TOPIC_TAXONOMY, **VISUAL_PROMPTS}
        self.person_score = float(person_score)
        self.appearance_threshold = float(appearance_threshold)
        self.extra_threshold = float(extra_threshold)
        self.min_person_area_ratio = float(min_person_area_ratio)
        self.gated_inset_black = bool(gated_inset_black)
        self.gated_inset_min_area_ratio = float(gated_inset_min_area_ratio)
        self._person_boxes = person_boxes
        self._model = None
        self._processor = None
        self._detector = None
        self._text_prompts: list[str] | None = None
        self._label_keys: list[str] | None = None
        self._builtin_keys: list[str] = []
        self._builtin_texts: list[str] = []
        self._scene_extra_keys: list[str] = []
        self._scene_extra_texts: list[str] = []
        self._appearance_keys: dict[str, str] = {}

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import CLIPModel, CLIPProcessor

        logger.info("Loading CLIP for visual topic scan: %s on %s", self.model_name, self.device)
        self._processor = CLIPProcessor.from_pretrained(self.model_name)
        self._model = CLIPModel.from_pretrained(self.model_name).to(self.device)
        self._model.eval()
        builtin_keys, builtin_prompts, extra_keys, extra_prompts = clip_label_groups(
            self.taxonomy
        )
        self._builtin_keys = builtin_keys
        self._builtin_texts = [NONE_PROMPT] + builtin_prompts
        self._appearance_keys = {
            key: appearance_class_for_label(key)
            for key in extra_keys
            if appearance_class_for_label(key)
        }
        scene_keys = [key for key in extra_keys if key not in self._appearance_keys]
        scene_prompts = [
            extra_prompts[extra_keys.index(key)] for key in scene_keys
        ]
        self._scene_extra_keys = scene_keys
        self._scene_extra_texts = [SCENE_NONE_PROMPT] + scene_prompts
        self._extra_keys = extra_keys
        self._extra_texts = [NONE_PROMPT] + extra_prompts
        self._label_keys = builtin_keys + extra_keys
        self._text_prompts = self._builtin_texts
        self._torch = torch

    def _ensure_detector(self) -> PersonBoxes | None:
        if self._person_boxes is not None:
            return self._person_boxes
        if not self._appearance_keys:
            return None
        if self._detector is not None:
            return self._detector
        try:
            from torchvision.models.detection import (
                FasterRCNN_ResNet50_FPN_V2_Weights,
                fasterrcnn_resnet50_fpn_v2,
            )
        except Exception as exc:
            logger.warning("Person detector unavailable: %s", exc)
            return None
        logger.info("Loading Faster R-CNN for person crops on %s", self.device)
        weights = FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
        model = fasterrcnn_resnet50_fpn_v2(weights=weights)
        model.to(self.device)
        model.eval()
        transform = weights.transforms()
        score_min = self.person_score

        def boxes_for(image: Image.Image) -> list[tuple[float, float, float, float]]:
            tensor = transform(image.convert("RGB")).to(self.device)
            with self._torch.no_grad():
                output = model([tensor])[0]
            found: list[tuple[float, float, float, float]] = []
            for box, label, score in zip(
                output["boxes"], output["labels"], output["scores"]
            ):
                if int(label) != 1 or float(score) < score_min:
                    continue
                x1, y1, x2, y2 = (float(part) for part in box.tolist())
                found.append((x1, y1, x2, y2))
            return found

        self._detector = boxes_for
        self._person_boxes = boxes_for
        return boxes_for

    def topics_for_image(self, image: Image.Image) -> list[str]:
        self._ensure_model()
        image = image.convert("RGB")
        hits: set[str] = set()
        hits.update(
            self._argmax_topic(
                image,
                self._builtin_keys,
                self._builtin_texts,
                self.threshold,
            )
        )
        hits.update(self._strict_visual_hits(image))
        hits.update(self._scene_extra_hits(image))
        hits.update(self._appearance_hits(image))
        return sorted(hits)

    def _person_crops_for_scoring(
        self, image: Image.Image, *, primary_only: bool = True
    ) -> list[tuple[Image.Image, float]]:
        detector = self._ensure_detector()
        crops_with_area: list[tuple[Image.Image, float]] = [
            (image, float(image.size[0] * image.size[1]))
        ]
        if detector is None:
            return crops_with_area
        width, height = image.size
        detected: list[tuple[Image.Image, float]] = []
        for x1, y1, x2, y2 in detector(image):
            pad = 8
            crop = image.crop(
                (
                    max(0, int(x1) - pad),
                    max(0, int(y1) - pad),
                    min(width, int(x2) + pad),
                    min(height, int(y2) + pad),
                )
            )
            if crop.size[0] < 16 or crop.size[1] < 16:
                continue
            area = max(1.0, (x2 - x1) * (y2 - y1))
            detected.append((crop, area))
        if not detected:
            return []
        if primary_only:
            return keep_primary_person_crops(detected, self.min_person_area_ratio)
        detected.sort(key=lambda item: item[1], reverse=True)
        return detected

    def _softmax_probs(self, image: Image.Image, texts: list[str]):
        assert self._model is not None
        assert self._processor is not None
        inputs = self._processor(
            text=texts,
            images=image,
            return_tensors="pt",
            padding=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            return self._model(**inputs).logits_per_image.softmax(dim=-1)[0]

    def _strict_group_hit(
        self,
        image: Image.Image,
        texts: list[str],
        *,
        min_index0_prob: float = 0.0,
    ) -> bool:
        probs = self._softmax_probs(image, texts)
        if float(probs[0]) < min_index0_prob:
            return False
        return int(probs.argmax()) == 0

    def _strict_visual_hits(self, image: Image.Image) -> list[str]:
        hits: list[str] = []
        crop_indexes = PERSON_CROP_STRICT_GROUP_INDEXES
        person_crops: list[tuple[Image.Image, float]] | None = None
        full_area = float(image.size[0] * image.size[1])
        for key, groups in strict_visual_groups().items():
            if key not in self.taxonomy:
                continue
            on_crops = crop_indexes.get(key, set())
            matched = False
            for index, texts in enumerate(groups):
                if index in on_crops:
                    continue
                if self._strict_group_hit(
                    image,
                    texts,
                    min_index0_prob=STRICT_FULL_FRAME_MIN_PROB.get(key, 0.0),
                ):
                    hits.append(key)
                    matched = True
                    break
            if matched:
                continue
            pending = [index for index in on_crops if index < len(groups)]
            if not pending:
                continue
            if person_crops is None:
                person_crops = self._person_crops_for_scoring(image, primary_only=False)
            min_prob = STRICT_PERSON_CROP_MIN_PROB.get(key, 0.0)
            min_area_ratio = MIN_PERSON_CROP_AREA_RATIO.get(key, 0.0)
            for index in pending:
                for crop, area in person_crops:
                    if min_area_ratio and (area / full_area) < min_area_ratio:
                        continue
                    if self._strict_group_hit(
                        crop,
                        groups[index],
                        min_index0_prob=min_prob,
                    ):
                        hits.append(key)
                        matched = True
                        break
                if matched:
                    break
        return hits

    def _scene_extra_hits(self, image: Image.Image) -> list[str]:
        if not self._scene_extra_keys:
            return []
        hits: list[str] = []
        remaining_keys: list[str] = []
        remaining_texts: list[str] = [SCENE_NONE_PROMPT]
        for key in self._scene_extra_keys:
            special = SCENE_CLASS_PROMPTS.get(key)
            if special:
                winner = self._argmax_class(
                    image,
                    ["hit"] + [f"d{i}" for i in range(len(special) - 1)],
                    special,
                )
                if winner == "hit":
                    hits.append(key)
            else:
                remaining_keys.append(key)
                remaining_texts.append(extra_visual_prompt(key))
        if remaining_keys:
            hits.extend(
                self._hits_for_texts(
                    image,
                    remaining_keys,
                    remaining_texts,
                    self.extra_threshold,
                )
            )
        return hits

    def _appearance_hits(self, image: Image.Image) -> list[str]:
        if not self._appearance_keys:
            return []
        hits: set[str] = set()
        hits.update(self._primary_appearance_hits(image))
        hits.update(self._gated_black_inset_hits(image))
        return sorted(hits)

    def _primary_appearance_hits(self, image: Image.Image) -> list[str]:
        crops_with_area = self._person_crops_for_scoring(image)
        if not crops_with_area:
            return []
        crops_with_area.sort(key=lambda item: item[1], reverse=True)
        wanted = {cls: key for key, cls in self._appearance_keys.items()}
        hits: set[str] = set()
        largest = crops_with_area[0][1]
        for crop, area in crops_with_area:
            groups = [APPEARANCE_ADULT_PROMPTS]
            if len(crops_with_area) == 1 or area <= APPEARANCE_CHILD_MAX_AREA_RATIO * largest:
                groups.append(APPEARANCE_CHILD_PROMPTS)
            for group in groups:
                classes = list(group)
                texts = [group[name] for name in classes]
                winner = self._argmax_class(crop, classes, texts)
                if winner in wanted:
                    hits.add(wanted[winner])
        return sorted(hits)

    def _gated_black_inset_hits(self, image: Image.Image) -> list[str]:
        if not self.gated_inset_black:
            return []
        black_label = next(
            (key for key, cls in self._appearance_keys.items() if cls == "black"),
            None,
        )
        if not black_label:
            return []
        all_crops = self._person_crops_for_scoring(image, primary_only=False)
        if not all_crops:
            return []
        inset_crops = gated_inset_person_crops(
            all_crops,
            primary_min_ratio=self.min_person_area_ratio,
            inset_min_ratio=self.gated_inset_min_area_ratio,
        )
        if not inset_crops:
            return []
        inset_crops.sort(key=lambda item: item[1], reverse=True)
        largest = max(area for _crop, area in all_crops)
        for crop, area in inset_crops:
            groups = [APPEARANCE_ADULT_PROMPTS]
            if area <= APPEARANCE_CHILD_MAX_AREA_RATIO * largest:
                groups.append(APPEARANCE_CHILD_PROMPTS)
            for group in groups:
                classes = list(group)
                texts = [group[name] for name in classes]
                winner = self._argmax_class(crop, classes, texts)
                if winner == "black":
                    return [black_label]
        return []

    def _argmax_class(
        self,
        image: Image.Image,
        classes: list[str],
        texts: list[str],
    ) -> str | None:
        if not classes:
            return None
        assert self._model is not None
        assert self._processor is not None
        inputs = self._processor(
            text=texts,
            images=image,
            return_tensors="pt",
            padding=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            probs = self._model(**inputs).logits_per_image.softmax(dim=-1)[0]
        best_index = max(range(len(classes)), key=lambda index: float(probs[index]))
        if float(probs[best_index]) < self.appearance_threshold:
            return None
        winner = classes[best_index]
        if winner == "other":
            return None
        return winner

    def _argmax_topic(
        self,
        image: Image.Image,
        keys: list[str],
        texts: list[str],
        threshold: float,
    ) -> list[str]:
        if not keys:
            return []
        assert self._model is not None
        assert self._processor is not None
        inputs = self._processor(
            text=texts,
            images=image,
            return_tensors="pt",
            padding=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            probs = self._model(**inputs).logits_per_image.softmax(dim=-1)[0]
        best_index = max(range(len(texts)), key=lambda index: float(probs[index]))
        if best_index == 0 or float(probs[best_index]) < threshold:
            return []
        return [keys[best_index - 1]]

    def _topics_for_crop(self, image: Image.Image) -> list[str]:
        self._ensure_model()
        return self._argmax_topic(
            image,
            self._builtin_keys,
            self._builtin_texts,
            self.threshold,
        )

    def _hits_for_texts(
        self,
        image: Image.Image,
        keys: list[str],
        texts: list[str],
        threshold: float,
    ) -> list[str]:
        if not keys:
            return []
        assert self._model is not None
        assert self._processor is not None
        inputs = self._processor(
            text=texts,
            images=image,
            return_tensors="pt",
            padding=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            probs = self._model(**inputs).logits_per_image.softmax(dim=-1)[0]
        hits = []
        for index, key in enumerate(keys, start=1):
            if float(probs[index]) >= threshold:
                hits.append(key)
        return hits

    def topics_for_path(self, path: str | Path) -> list[str]:
        with Image.open(path) as image:
            return self.topics_for_image(image)


def _frame_in_segment(timestamp: float, start: float, end: float, *, is_last: bool) -> bool:
    """Include bucket boundaries so a sample at 35s tags both 30–35 and 35–40."""
    _ = is_last
    return start <= timestamp <= end


def attach_visual_topics(
    segments: list[dict],
    frames: list[dict[str, Any]],
    scanner: ClipTopicScanner,
) -> list[dict]:
    """Union CLIP labels from frames that fall inside each segment."""
    if not frames:
        return segments
    cache: dict[str, list[str]] = {}
    labeled: list[tuple[float, list[str]]] = []
    for frame in frames:
        path = frame.get("frame_path")
        timestamp = frame.get("time")
        if not path or timestamp is None or not Path(path).is_file():
            continue
        try:
            if path not in cache:
                cache[path] = scanner.topics_for_path(path)
            topics = cache[path]
        except Exception as exc:
            logger.warning("Visual topic scan failed for %s: %s", path, exc)
            continue
        if topics:
            labeled.append((float(timestamp), topics))

    last_index = len(segments) - 1
    updated = []
    for index, seg in enumerate(segments):
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", 0.0))
        topics = set(seg.get("topics") or [])
        is_last = index == last_index
        for timestamp, frame_topics in labeled:
            if _frame_in_segment(timestamp, start, end, is_last=is_last):
                topics.update(frame_topics)
        row = dict(seg)
        row["topics"] = sorted(topics)
        updated.append(row)
    return updated
