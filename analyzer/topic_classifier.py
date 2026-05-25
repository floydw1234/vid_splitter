"""
LLM Topic Classifier
Uses an LLM via OpenAI-compatible API to classify video segments for topics
beyond NSFW: profanity, ideology, religion, politics, etc.
"""
import json
import logging
from typing import List, Dict

import httpx

logger = logging.getLogger(__name__)

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
    "lgbtq": "LGBTQ+ themes, identity, relationships",
    "racism": "racial discrimination, prejudice, slurs",
    "mental_health": "mental illness, therapy, depression, anxiety",
    "death_grief": "death, mourning, funerals, loss",
    "war_military": "warfare, soldiers, military operations",
    "crime": "theft, robbery, illegal activities",
    "violence_domestic": "domestic abuse, family violence",
    "self_harm": "suicide, self-injury, eating disorders",
}

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
        self.client = httpx.Client(timeout=120.0)
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
    ) -> List[Dict]:
        """Classify multiple segments for topics.

        Args:
            segments: List of segment dicts with 'transcript' key.

        Returns:
            Segments with 'topics' key added.
        """
        logger.info(f"Classifying {len(segments)} segments with LLM...")
        for seg in segments:
            transcript = seg.get("transcript", "")
            start = seg.get("start_time", 0)
            end = seg.get("end_time", 0)
            topics = self.classify_segment(transcript, start, end)
            seg["topics"] = topics
        return segments

    def _parse_topics(self, content: str) -> List[str]:
        """Parse topic list from LLM response."""
        # Try to find JSON array in response
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1 and end > start:
            json_str = content[start:end + 1]
            try:
                topics = json.loads(json_str)
                if isinstance(topics, list):
                    # Validate topics are in our taxonomy
                    return [t for t in topics if t in self.topics]
            except json.JSONDecodeError:
                pass

        # Fallback: try to extract topic names
        topics = []
        for topic_name in self.topics:
            if topic_name in content:
                topics.append(topic_name)
        return topics
