"""
Transcript topic classifiers.

Default is the local MiniLM embedding service on :9009 (CPU, ~80MB). That is a
closed-set similarity check against the taxonomy, not a chat LLM, so it does
not steal GPU from Wan and does not depend on the 8081 `latest` route.

`--topic-backend llm` still talks to an OpenAI-compatible chat API if you
explicitly want that.
"""
import json
import logging
import os
import re
from typing import List, Dict

logger = logging.getLogger(__name__)


def _httpx_client(timeout: float):
    import httpx

    return httpx.Client(timeout=timeout)

# Topic labels are intentionally lightweight heuristics.
# Default topic taxonomy - extend as needed
TOPIC_TAXONOMY = {
    "profanity": "swearing, cursing, explicit language",
    "religion_christianity": "Christian themes, Bible references, church, prayer, God, Jesus",
    "religion_general": "religious themes, spirituality, worship",
    "politics": "political discussion, government, elections, policy",
    "feminism": "feminist themes, gender equality, women's rights",
    "masculinity": "masculine themes, male bonding, traditional gender roles",
    "drugs": "drug use, substance abuse, intoxication",
    "alcohol": "drinking, parties, bars",
    "smoking": "tobacco, vaping, cigarettes",
    "sex_education": "sex education, contraception, reproductive health",
    "lgbtq": "explicit LGBTQ identity, pride flags, or same-sex romance; not rainbow toys or colors",
    "racism": "racial discrimination, prejudice, slurs",
    "mental_health": "mental illness, therapy, depression, anxiety",
    "death_grief": "death, mourning, funerals, loss",
    "war_military": "warfare, soldiers, military operations",
    "crime": "theft, robbery, illegal activities",
    "violence_domestic": "physical assault in a family, hitting or beating someone; not soothing a baby",
    "self_harm": "suicide, self-injury, eating disorders",
}


def taxonomy_with_extras(
    extra: tuple[str, ...] | list[str] = (),
    base: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Copy a taxonomy and add author/Jellyfin labels that are not already present."""
    taxonomy = dict(base or TOPIC_TAXONOMY)
    for label in extra:
        key = str(label).strip()
        if key and key not in taxonomy:
            taxonomy[key] = f'content matching "{key}"'
    return taxonomy


def visual_only_transcript_labels() -> frozenset[str]:
    """Appearance tags are CLIP-only; never classify them from transcript embeddings."""
    from analyzer.visual_topics import APPEARANCE_LABELS

    return frozenset(APPEARANCE_LABELS)


def transcript_topic_taxonomy(
    extra: tuple[str, ...] | list[str] = (),
    base: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Taxonomy for transcript classifiers, minus visual-only appearance labels."""
    skip = visual_only_transcript_labels()
    taxonomy = taxonomy_with_extras(extra, base)
    return {key: value for key, value in taxonomy.items() if key not in skip}


# Color words in kids' media ("black and white dog") must not tag appearance topics.
_COLOR_DESCRIPTOR_RE = re.compile(
    r"\bblack and white\b|"
    r"\bblack\s+(?:and\s+)?(?:white\s+)?(?:dog|dogs|puppy|puppies|kitten|cat|cats|"
    r"bird|birds|horse|horses|cow|cows|sheep|duck|ducks|bear|bears|fish|"
    r"spot|spots|stripe|stripes|bow|bows)\b",
    flags=re.IGNORECASE,
)
_ANIMAL_PLAY_RE = re.compile(
    r"\bwoof\b|\bdockie|\bdoggies?\b|\bpretending to be (?:a )?(?:dog|dockie|doggie)",
    flags=re.IGNORECASE,
)


def filter_misleading_transcript_topics(
    transcript: str,
    topics: List[str],
) -> List[str]:
    """Drop transcript topics contradicted by obvious benign phrasing."""
    if not topics:
        return topics
    drop: set[str] = set()
    if _COLOR_DESCRIPTOR_RE.search(transcript):
        drop.update({"black people", "racism"})
    if _ANIMAL_PLAY_RE.search(transcript):
        drop.update({"lgbtq"})
    if not drop:
        return topics
    return [name for name in topics if name not in drop]


DEFAULT_TOPIC_BACKEND = "embed"
DEFAULT_EMBED_URL = "http://localhost:9009"
DEFAULT_EMBED_MODEL = "minilm-fast"
DEFAULT_EMBED_MARGIN = 0.08
NONE_EMBED_PROMPT = (
    "A transcript of ordinary everyday conversation with no politics, religion, "
    "drugs, sex, violence, profanity, or identity activism"
)
# MiniLM is weak on short swear clips; keyword hits cover that one class.
PROFANITY_KEYWORDS = (
    "fuck",
    "fucking",
    "shit",
    "bitch",
    "asshole",
    "cunt",
    "motherfucker",
    "goddamn",
    "god damn",
    "damn it",
)


def make_topic_classifier(
    topics: Dict[str, str] | None = None,
    backend: str | None = None,
    **kwargs,
):
    """Build the transcript topic classifier for this analyze run."""
    chosen = (backend or os.environ.get("TOPIC_BACKEND") or DEFAULT_TOPIC_BACKEND).strip().lower()
    taxonomy = topics or TOPIC_TAXONOMY
    if chosen in {"llm", "chat", "openai"}:
        return LLMTopicClassifier(
            api_url=kwargs.get("api_url") or os.environ.get("TOPIC_API_URL", "http://localhost:8081"),
            model=kwargs.get("model") or os.environ.get("TOPIC_MODEL", "latest"),
            topics=taxonomy,
        )
    if chosen not in {"embed", "embedding", "embeddings"}:
        logger.warning("Unknown topic backend %r; using embed", chosen)
    return EmbedTopicClassifier(
        api_url=kwargs.get("api_url") or os.environ.get("TOPIC_EMBED_URL", DEFAULT_EMBED_URL),
        model=kwargs.get("model") or os.environ.get("TOPIC_EMBED_MODEL", DEFAULT_EMBED_MODEL),
        topics=taxonomy,
        margin=float(kwargs.get("margin") or os.environ.get("TOPIC_EMBED_MARGIN", DEFAULT_EMBED_MARGIN)),
        client=kwargs.get("client"),
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


# System prompt for topic classification
CLASSIFICATION_PROMPT = """You are a video content classifier. Analyze the following transcript segment and classify it for topics.

Available topics (use EXACTLY these names):
{topics}

Rules:
- Only assign topics that are CLEARLY present in the transcript
- Be conservative - if unsure, don't assign the topic
- Return ONLY a JSON array of topic strings (no explanation)
- Use EXACTLY the topic names above (e.g., "profanity", not "Profanity")
- Return empty array [] if no topics match

Example output: ["profanity", "alcohol"]
Example output: []

Transcript:
{transcript}

Topics present:"""


class LLMTopicClassifier:
    """Classifies video segments for topics using an LLM."""

    def __init__(
        self,
        api_url: str = "http://localhost:8081",
        model: str = "latest",
        topics: Dict[str, str] | None = None,
    ):
        """Initialize the LLM topic classifier.

        Args:
            api_url: OpenAI-compatible API URL.
            model: Model name to use.
            topics: Custom topic taxonomy. Uses default if None.
        """
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.topics = topics or TOPIC_TAXONOMY
        self.client = _httpx_client(120.0)
        logger.info(f"LLM Topic Classifier initialized: {self.api_url} ({self.model})")

    def classify_segment(
        self,
        transcript: str,
        start_time: float,
        end_time: float,
    ) -> List[str]:
        """Classify a transcript segment for topics.

        Args:
            transcript: The transcript text for this segment.
            start_time: Segment start time in seconds.
            end_time: Segment end time in seconds.

        Returns:
            List of topic strings that match the segment.
        """
        if not transcript or not transcript.strip():
            return []

        # Build topic list for prompt
        topic_list = "\n".join(f"- {k}: {v}" for k, v in self.topics.items())

        prompt = CLASSIFICATION_PROMPT.format(
            topics=topic_list,
            transcript=transcript[:2000],  # Limit length
        )

        try:
            response = self.client.post(
                f"{self.api_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1000,
                },
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Parse JSON array from response
            topics = self._parse_topics(content)
            logger.info(
                f"  [{start_time:.1f}-{end_time:.1f}s] LLM response: {content[:200]}"
            )
            logger.info(
                f"  [{start_time:.1f}-{end_time:.1f}s] Parsed topics: {topics}"
            )
            return topics

        except Exception as e:
            logger.warning(f"LLM classification failed: {e}")
            return []

    def classify_segments(
        self,
        segments: List[Dict],
        batch_size: int = 8,
    ) -> List[Dict]:
        """Classify multiple segments for topics.

        Batches transcripts into a few LLM calls so a full movie still gets
        labeled. Falls back to one request per segment if a batch parse fails.
        """
        logger.info(f"Classifying {len(segments)} segments with LLM...")
        start = 0
        while start < len(segments):
            chunk = segments[start : start + batch_size]
            labeled = self._classify_batch(chunk)
            if labeled is None:
                for seg in chunk:
                    seg["topics"] = self.classify_segment(
                        seg.get("transcript", ""),
                        seg.get("start_time", 0),
                        seg.get("end_time", 0),
                    )
            else:
                for seg, topics in zip(chunk, labeled):
                    seg["topics"] = topics
            start += batch_size
        return segments

    def _classify_batch(self, segments: List[Dict]) -> List[List[str]] | None:
        if not segments:
            return []
        numbered = []
        for i, seg in enumerate(segments):
            transcript = str(seg.get("transcript") or "").strip()[:800]
            start = seg.get("start_time", 0)
            end = seg.get("end_time", 0)
            numbered.append(f"{i}. [{start:.1f}-{end:.1f}s] {transcript or '(empty)'}")
        topic_list = "\n".join(f"- {k}: {v}" for k, v in self.topics.items())
        prompt = (
            "You are a video content classifier. Classify each numbered transcript.\n"
            "Available topics (use EXACTLY these names):\n"
            f"{topic_list}\n\n"
            "Return ONLY a JSON array with one array of topic names per transcript, "
            f"length {len(segments)}. Example: [[\"profanity\"], [], [\"religion_christianity\"]]\n"
            "Rules: only clearly present topics; conservative; empty array if none match.\n\n"
            + "\n".join(numbered)
        )
        try:
            response = self.client.post(
                f"{self.api_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 2000,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
            parsed = self._parse_batch_topics(content, len(segments))
            if parsed is None:
                logger.warning("LLM batch topic response was not a valid array; falling back")
            return parsed
        except Exception as exc:
            logger.warning(f"LLM batch classification failed: {exc}")
            return None

    def _parse_batch_topics(self, content: str, count: int) -> List[List[str]] | None:
        candidates: list[str] = [content]
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)\s*```",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            candidates.append(match.group(1))
        for raw in candidates:
            parsed = self._parse_json_value(raw)
            if isinstance(parsed, list) and len(parsed) == count:
                return [self._coerce_topic_list(item) for item in parsed]
            if isinstance(parsed, dict):
                rows = []
                for i in range(count):
                    if str(i) not in parsed and i not in parsed:
                        break
                    rows.append(self._coerce_topic_list(parsed.get(str(i), parsed.get(i))))
                if len(rows) == count:
                    return rows
        return None

    def _coerce_topic_list(self, value: object) -> List[str]:
        if isinstance(value, str):
            resolved = self._resolve_topic_name(value)
            return [resolved] if resolved else []
        if not isinstance(value, list):
            return []
        topics: List[str] = []
        seen: set[str] = set()
        for item in value:
            resolved = self._resolve_topic_name(item) if isinstance(item, str) else None
            if resolved and resolved not in seen:
                seen.add(resolved)
                topics.append(resolved)
        return topics

    def _resolve_topic_name(self, name: str) -> str | None:
        if name in self.topics:
            return name
        lowered = name.strip().lower()
        for key in self.topics:
            if key.lower() == lowered:
                return key
        return None

    def _parse_json_value(self, value: str) -> object | None:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None


    def _parse_topics(self, content: str) -> List[str]:
        """Parse topic list from LLM response."""
        parsed = self._parse_topic_list(content)
        if parsed is not None:
            return parsed

        for match in re.finditer(
            r"```(?:json)?\s*(.*?)\s*```",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            parsed = self._parse_topic_list(match.group(1))
            if parsed is not None:
                return parsed

        for match in re.finditer(r"\[[^\[\]]*?\]", content, flags=re.DOTALL):
            parsed = self._parse_topic_list(match.group(0))
            if parsed is not None:
                return parsed

        # Fallback: try to extract topic names
        topics = []
        for topic_name in self.topics:
            if topic_name in content:
                topics.append(topic_name)
        return topics

    def _parse_topic_list(self, value: str) -> List[str] | None:
        """Return validated topics for a JSON array string, else None."""
        try:
            topics = json.loads(value)
        except json.JSONDecodeError:
            return None

        if not isinstance(topics, list):
            return None

        return self._coerce_topic_list(topics)


class EmbedTopicClassifier:
    """Zero-shot transcript topics via the local MiniLM embedding service."""

    def __init__(
        self,
        api_url: str = DEFAULT_EMBED_URL,
        model: str = DEFAULT_EMBED_MODEL,
        topics: Dict[str, str] | None = None,
        margin: float = DEFAULT_EMBED_MARGIN,
        client=None,
    ):
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.topics = topics or TOPIC_TAXONOMY
        self.margin = float(margin)
        self.client = client or _httpx_client(180.0)
        self._label_names: list[str] | None = None
        self._label_vectors: list[list[float]] | None = None
        self._none_vector: list[float] | None = None
        logger.info(
            "Embed topic classifier: %s model=%s margin=%.3f",
            self.api_url,
            self.model,
            self.margin,
        )

    def classify_segment(
        self,
        transcript: str,
        start_time: float,
        end_time: float,
    ) -> List[str]:
        labeled = self.classify_segments(
            [{"transcript": transcript, "start_time": start_time, "end_time": end_time}]
        )
        return labeled[0].get("topics", []) if labeled else []

    def classify_segments(
        self,
        segments: List[Dict],
        batch_size: int = 32,
    ) -> List[Dict]:
        logger.info("Classifying %s segments with embeddings...", len(segments))
        self._ensure_labels()
        indexed: list[tuple[int, str]] = []
        for i, seg in enumerate(segments):
            text = str(seg.get("transcript") or "").strip()[:2000]
            if text:
                indexed.append((i, text))
            seg["topics"] = self._keyword_topics(text) if text else []
        if self._label_vectors is None or self._none_vector is None:
            return segments
        try:
            for start in range(0, len(indexed), batch_size):
                chunk = indexed[start : start + batch_size]
                vectors = self._embed([text for _, text in chunk])
                for (idx, text), vector in zip(chunk, vectors):
                    found = self._topics_for_vector(vector)
                    extra = self._keyword_topics(text)
                    merged: list[str] = []
                    seen: set[str] = set()
                    for name in found + extra:
                        if name not in seen:
                            seen.add(name)
                            merged.append(name)
                    merged = filter_misleading_transcript_topics(text, merged)
                    segments[idx]["topics"] = merged
                    start_time = segments[idx].get("start_time", 0)
                    end_time = segments[idx].get("end_time", 0)
                    logger.info(
                        "  [%.1f-%.1f s] embed topics: %s",
                        start_time,
                        end_time,
                        merged,
                    )
        except Exception as exc:
            logger.warning("Embedding transcript topics failed: %s", exc)
        return segments

    def _topic_prompt(self, name: str) -> str:
        return f"A transcript about {name}: {self.topics[name]}"

    def _ensure_labels(self) -> None:
        if self._label_vectors is not None:
            return
        names = list(self.topics)
        prompts = [self._topic_prompt(name) for name in names] + [NONE_EMBED_PROMPT]
        try:
            vectors = self._embed(prompts)
        except Exception as exc:
            logger.warning("Embedding topic labels failed: %s", exc)
            return
        if len(vectors) != len(prompts):
            logger.warning("Embedding service returned %s vectors for %s prompts", len(vectors), len(prompts))
            return
        self._label_names = names
        self._label_vectors = vectors[:-1]
        self._none_vector = vectors[-1]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.post(
            f"{self.api_url}/embed",
            json={"texts": texts, "model_id": self.model, "normalize": True},
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list):
            raise RuntimeError(f"Unexpected embed response keys: {list(payload)}")
        return embeddings

    def _topics_for_vector(self, vector: list[float]) -> List[str]:
        if not self._label_names or not self._label_vectors or self._none_vector is None:
            return []
        none_sim = cosine_similarity(vector, self._none_vector)
        scored: list[tuple[str, float]] = []
        for name, label in zip(self._label_names, self._label_vectors):
            delta = cosine_similarity(vector, label) - none_sim
            if delta >= self.margin:
                scored.append((name, delta))
        scored.sort(key=lambda item: item[1], reverse=True)
        if not scored:
            return []
        best = scored[0][1]
        # Keep clear winners only; near-ties vs none stay dropped by margin.
        return [name for name, delta in scored if (best - delta) <= 0.03]

    def _keyword_topics(self, transcript: str) -> List[str]:
        if "profanity" not in self.topics:
            return []
        lowered = f" {transcript.lower()} "
        for word in PROFANITY_KEYWORDS:
            if word in lowered:
                return ["profanity"]
        return []
