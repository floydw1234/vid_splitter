"""
Profile Resolver
Resolves user profiles to segment actions based on filters.

Supports:
1. Legacy profiles (birthday + sex → child/teen/adult)
2. Custom profiles (topic → action mapping)
"""
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# Default profiles with filter mappings
DEFAULT_PROFILES = {
    "child": {
        "name": "Child (under 13)",
        "description": "Blocks all mature content",
        "filters": {
            "nudity": "swap",
            "violence": "blur",
            "language": "mute",
            "gore": "skip",
            "fear": "skip",
            "profanity": "skip",
            "drugs": "skip",
            "alcohol": "skip",
        },
    },
    "teen_m": {
        "name": "Teen Male (13-17)",
        "description": "Blocks nudity and gore",
        "filters": {
            "nudity": "swap",
            "gore": "skip",
            "profanity": "mute",
        },
    },
    "teen_f": {
        "name": "Teen Female (13-17)",
        "description": "Blocks nudity and violence",
        "filters": {
            "nudity": "swap",
            "violence": "blur",
            "profanity": "mute",
        },
    },
    "adult": {
        "name": "Adult (18+)",
        "description": "No filters",
        "filters": {},
    },
}


class ProfileResolver:
    """Resolves user profiles to segment actions."""

    def __init__(self, profiles: Dict | None = None):
        """Initialize with profiles.

        Args:
            profiles: Dict of profile_name → profile_data.
                     Uses DEFAULT_PROFILES if None.
        """
        self.profiles = profiles or DEFAULT_PROFILES

    def load_profile_from_json(self, json_path: Path) -> Dict:
        """Load a profile from a JSON file.

        Supports two formats:
        1. Legacy: {"birthday": "...", "sex": "..."} → resolved to child/teen/adult
        2. Custom: {"name": "...", "filters": {...}} → used directly

        Args:
            json_path: Path to the JSON file.

        Returns:
            Profile data with filters.
        """
        data = json.loads(json_path.read_text())

        # Legacy format: resolve from birthday + sex
        if "birthday" in data:
            return self._resolve_legacy_profile(data)

        # Custom format: use directly
        if "name" in data and "filters" in data:
            return data

        raise ValueError(f"Invalid profile format: {data}")

    def _resolve_legacy_profile(self, data: Dict) -> Dict:
        """Resolve legacy profile (birthday + sex) to child/teen/adult."""
        birthday = datetime.strptime(data["birthday"], "%Y-%m-%d").date()
        sex = data.get("sex", "any").lower()
        today = date.today()
        age = (today - birthday).days / 365.25

        if age < 13:
            profile_name = "child"
        elif age < 18:
            profile_name = f"teen_{sex[0]}" if sex in ("male", "female", "m", "f") else "teen_m"
        else:
            profile_name = "adult"

        logger.info(f"Resolved profile: {profile_name} (age={age:.1f}, sex={sex})")
        return {
            "name": profile_name,
            "filters": self.profiles.get(profile_name, {}).get("filters", {}),
        }

    def resolve_segment_action(
        self,
        profile: Dict,
        segment: Dict,
    ) -> str:
        """Resolve the action for a segment based on profile filters.

        Args:
            profile: Profile data with filters.
            segment: Segment data with tags and topics.

        Returns:
            Action: "play", "swap", "skip", "mute", or "blur".
        """
        filters = profile.get("filters", {})
        if not filters:
            return "play"

        # Check segment tags (from NSFW detection)
        tags = segment.get("tags", [])
        topics = segment.get("topics", [])

        # Combine tags and topics
        all_labels = set(tags) | set(topics)

        # Find most restrictive action
        actions = []
        for label in all_labels:
            if label in filters:
                actions.append(filters[label])

        if not actions:
            return "play"

        # Priority: skip > swap > blur > mute > play
        priority = {"skip": 5, "swap": 4, "blur": 3, "mute": 2, "play": 1}
        most_restrictive = max(actions, key=lambda a: priority.get(a, 0))

        return most_restrictive

    def resolve_segments(
        self,
        profile: Dict,
        segments: List[Dict],
    ) -> List[Dict]:
        """Resolve actions for all segments.

        Args:
            profile: Profile data with filters.
            segments: List of segments.

        Returns:
            Segments with resolved actions.
        """
        resolved = []
        for seg in segments:
            action = self.resolve_segment_action(profile, seg)
            resolved.append({**seg, "resolved_action": action})
        return resolved
