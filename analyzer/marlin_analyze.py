"""
Marlin-2B Video Analyzer
Uses NemoStation/Marlin-2B VLM for unified video content analysis.

Single-pass approach: feeds video to Marlin-2B's .caption() method, which
produces timestamped scene + event captions. These are mapped to BVF segments
with risk tags via keyword matching.

Usage:
  python marlin_analyze.py "path/to/video.mp4" [--output-dir /path/to/output]
"""
import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vid_splitter.bvf_muxer import BvfMuxer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Tags that map to filter categories
VALID_TAGS = {"language", "nudity", "violence", "gore", "fear"}

# Keyword mapping from Marlin captions to our tag taxonomy
TAG_KEYWORDS = {
    "nudity": [
        "nude", "naked", "naked", "nude", "naked", "nude", "naked",
        "breast", "boob", "tit", "tits", "nipple", "genital", "penis",
        "vagina", "ass", "butt", "buttocks", "anal", "sexual", "sex",
        "nude", "naked", "exposed", "topless", "bottomless",
        "underwear", "lingerie", "bra", "panty", "thong",
        "masturbat", "orgasm", "ejaculat", "cum", "lubricat",
        "intimate", "seductiv", "sensual", "erotic", "porn",
        "strip", "burlesque", "nude art", "nude model",
    ],
    "violence": [
        "fight", "hit", "punch", "kick", "slap", "beat", "assault",
        "attack", "weapon", "gun", "knife", "sword", "stab", "shoot",
        "bullet", "bomb", "explode", "destroy", "crash", "accident",
        "war", "battle", "combat", "military", "soldier", "army",
        "hurt", "injure", "wound", "blood", "bleed", "torture",
        "abuse", "domestic violence", "mugging", "robbery",
        "violent", "aggressive", "brutal", "savage",
    ],
    "gore": [
        "gore", "gory", "blood", "bloody", "bleed", "wound", "injury",
        "mutilat", "dismember", "corpse", "dead body", "skull", "bone",
        "viscera", "organ", "flesh", "skinned", "flayed",
        "graphically", "gruesome", "macabre", "horrifying",
        "execute", "execution", "behead", "decapitat",
        "torture", "mutilation", "cannibal", "flesh wound",
    ],
    "fear": [
        "scary", "frightening", "terrifying", "horror", "creepy",
        "eerie", "spooky", "haunted", "ghost", "demon", "monster",
        "zombie", "vampire", "werewolf", "supernatural",
        "threatening", "menacing", "danger", "peril",
        "suspense", "tension", "thriller", "chase", "pursuit",
        "jump scare", "nightmare", "nightmarish", "disturbing",
        "shocking", "alarming", "panic", "terror", "fear",
        "anxiety", "anxious", "nervous", "tense",
    ],
    "language": [
        "fuck", "shit", "damn", "hell", "bastard", "asshole", "bitch",
        "cunt", "pussy", "dick", "cock", "nigger", "nigga", "faggot",
        "whore", "slut", "retard", "motherfucker", "bullshit",
        "crap", "piss", "screw", "suck", "god damn", "jesus christ",
        "profanity", "swear", "cursing", "vulgar", "obscene",
        "rude", "inappropriate", "offensive", "insult",
    ],
}


# Default profiles baked into every manifest
DEFAULT_PROFILES = {
    "child": {
        "label": "Child (under 13)",
        "age": 10,
        "gender": "any",
        "filters": ["nudity", "violence", "language", "fear", "gore"],
    },
    "teen_m": {
        "label": "Teen male (13-17)",
        "age": 15,
        "gender": "male",
        "filters": ["nudity", "gore"],
    },
    "teen_f": {
        "label": "Teen female (13-17)",
        "age": 15,
        "gender": "female",
        "filters": ["nudity", "violence"],
    },
    "adult": {
        "label": "Adult (18+)",
        "age": 18,
        "gender": "any",
        "filters": [],
    },
}


class MarlinAnalyzer:
    """Analyzes a video using Marlin-2B VLM and generates a BVF manifest."""

    MODEL_NAME = "NemoStation/Marlin-2B"

    def __init__(
        self,
        video_path: str,
        output_dir: str | None = None,
        device: str | None = None,
    ):
        self.video_path = Path(video_path).resolve()
        self.output_dir = Path(output_dir) if output_dir else self.video_path.parent
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.last_bvf_path: Path | None = None

        logger.info(f"Loading Marlin-2B model: {self.MODEL_NAME}")
        logger.info(f"Using device: {self.device}")
        self._load_model()
        logger.info("Model loaded. Ready to analyze.")

    def _load_model(self) -> None:
        """Load Marlin-2B model with trust_remote_code for custom .caption()/.find()."""
        try:
            self.model = AutoModelForCausalLM.from_pretrained(
                self.MODEL_NAME,
                trust_remote_code=True,
                dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
                device_map={"": self.device},
            )
            self.model.eval()
        except Exception as e:
            logger.error(f"Failed to load Marlin-2B model: {e}")
            raise

    def analyze(self) -> dict:
        """Run the Marlin-2B analysis pipeline and return the manifest dict."""
        logger.info(f"Analyzing: {self.video_path}")

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        # 1. Get video metadata
        duration = self._get_duration()
        logger.info(f"Video duration: {duration:.1f}s")

        # 2. Run Marlin-2B caption mode
        logger.info("Running Marlin-2B caption analysis...")
        caption_result = self.model.caption(str(self.video_path), max_new_tokens=2048)
        logger.info(f"Scene: {caption_result.get('scene', '')}")
        events = caption_result.get("events", [])
        logger.info(f"Marlin-2B identified {len(events)} events")

        # Log all raw events
        logger.info("=" * 60)
        logger.info("RAW EVENTS FROM MARLIN-2B:")
        logger.info("=" * 60)
        for ev in events:
            logger.info(
                f"  <{ev.get('start', 0):.1f} - {ev.get('end', 0):.1f}> "
                f"{ev.get('description', '')}"
            )
        logger.info("=" * 60)

        # 3. Map events to BVF segments with tag classification
        segments = self._build_segments(events, duration)
        logger.info(f"Built {len(segments)} segments")

        # Log classified segments
        logger.info("=" * 60)
        logger.info("CLASSIFIED SEGMENTS:")
        logger.info("=" * 60)
        for seg in segments:
            tags_str = f" tags={seg['tags']}" if seg["tags"] else ""
            logger.info(
                f"  {seg['id']}: {seg['start_time']:.1f}s - {seg['end_time']:.1f}s "
                f"[{seg['risk']}]{tags_str}"
            )
        logger.info("=" * 60)

        # 4. Attach media packets
        self._attach_media_packets(segments)

        # 5. Build and save manifest
        manifest = self._build_manifest(segments, duration)
        output_bvf = self._save_bvf(manifest)
        self.last_bvf_path = output_bvf
        logger.info(f"BVF saved to: {output_bvf}")

        return manifest

    def _get_duration(self) -> float:
        """Get video duration via FFprobe."""
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(self.video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())

    def _classify_event(self, description: str) -> list[str]:
        """Classify an event description into our tag taxonomy via keyword matching.

        Uses word-boundary matching to avoid false positives like "hit" in "white".
        Language (profanity) requires 1 match (explicit words). Other tags require 2.

        Args:
            description: Natural language description from Marlin-2B.

        Returns:
            List of matching tags from VALID_TAGS.
        """
        desc_lower = description.lower()
        tags = {}  # tag -> count of matching keywords

        for tag, keywords in TAG_KEYWORDS.items():
            match_count = 0
            matched_kws = []
            for kw in keywords:
                # Use word boundary matching to avoid substring false positives
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                if re.search(pattern, desc_lower):
                    match_count += 1
                    matched_kws.append(kw)
            # Language/profanity: 1 match is enough (explicit words)
            # Other tags: 2+ matches to reduce false positives
            threshold = 1 if tag == "language" else 2
            if match_count >= threshold:
                tags[tag] = (match_count, matched_kws)

        # Log matches for debugging
        if tags:
            kw_summary = ", ".join(f"{t}({c}x: {', '.join(kws)})" for t, (c, kws) in tags.items())
            logger.info(f"  Tag matches: {kw_summary}")
            logger.info(f"    for: '{description[:80]}'")

        return sorted(tags.keys())

    def _build_segments(self, events: list[dict], duration: float) -> list[dict]:
        """Convert Marlin-2B events into BVF segments with gap filling.

        Marlin-2B events have: start, end, description
        We add: tags, risk, action
        """
        if not events:
            # No events detected — one safe segment covering the whole video
            return [{
                "id": "seg_001",
                "start_time": 0,
                "end_time": duration,
                "tags": [],
                "risk": "safe",
                "action": "play",
            }]

        # Sort by start time
        events.sort(key=lambda e: e.get("start", 0))

        # Classify and build segments
        classified = []
        for event in events:
            desc = event.get("description", "")
            tags = self._classify_event(desc)
            risk = "mature" if tags else "safe"

            classified.append({
                "start_time": float(event.get("start", 0)),
                "end_time": float(event.get("end", duration)),
                "description": desc,
                "tags": tags,
                "risk": risk,
                "action": "swap" if risk == "mature" else "play",
            })

        # Fill gaps and ensure contiguous coverage
        segments = []
        prev_end = 0.0

        for seg in classified:
            # Fill gap before this segment
            if seg["start_time"] > prev_end:
                segments.append({
                    "id": "",
                    "start_time": round(prev_end, 2),
                    "end_time": round(seg["start_time"], 2),
                    "tags": [],
                    "risk": "safe",
                    "action": "play",
                })

            segments.append({
                "id": "",
                "start_time": round(seg["start_time"], 2),
                "end_time": round(seg["end_time"], 2),
                "tags": seg["tags"],
                "risk": seg["risk"],
                "action": seg["action"],
            })
            prev_end = seg["end_time"]

        # Fill gap after last segment
        if prev_end < duration:
            segments.append({
                "id": "",
                "start_time": round(prev_end, 2),
                "end_time": round(duration, 2),
                "tags": [],
                "risk": "safe",
                "action": "play",
            })

        # Renumber segments
        for i, seg in enumerate(segments):
            seg["id"] = f"seg_{i + 1:03d}"

        return segments

    def _build_manifest(self, segments: list[dict], duration: float) -> dict:
        """Build the BVF manifest dict."""
        return {
            "movie_id": self.video_path.stem,
            "movie_path": str(self.video_path),
            "duration_seconds": round(duration, 2),
            "analyzed_at": datetime.utcnow().isoformat(),
            "analyzer": "marlin-2b",
            "profiles": DEFAULT_PROFILES,
            "segments": segments,
        }

    def _attach_media_packets(self, segments: list[dict]) -> None:
        """Embed MPEG-TS media payloads for every segment."""
        import tempfile as tf

        with tf.TemporaryDirectory(prefix="bvf_marlin_segments_") as tmp:
            tmp_dir = Path(tmp)
            for seg in segments:
                segment_path = tmp_dir / f"{seg['id']}.ts"
                self._remux_segment(seg["start_time"], seg["end_time"], segment_path)
                seg["video_packets"] = [{
                    "pts_ms": int(seg["start_time"] * 1000),
                    "data": segment_path.read_bytes(),
                }]
                seg["audio_packets"] = []

    def _remux_segment(self, start_time: float, end_time: float, output_path: Path) -> None:
        """Extract a segment from the video as MPEG-TS."""
        duration = max(0.001, end_time - start_time)
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{start_time:.3f}",
                "-i", str(self.video_path),
                "-t", f"{duration:.3f}",
                "-map", "0:v:0",
                "-map", "0:a?",
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                "-f", "mpegts",
                str(output_path),
            ],
            check=True,
            capture_output=True,
        )

    def _save_bvf(self, manifest: dict) -> Path:
        """Write manifest into a BVF container."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_bvf = self.output_dir / f"{self.video_path.stem}.bvf"
        muxer = BvfMuxer(
            movie_id=manifest["movie_id"],
            title=self.video_path.stem,
        )
        return muxer.write_bvf(
            output_path=output_bvf,
            segments=manifest["segments"],
            duration_seconds=manifest["duration_seconds"],
            profiles=manifest["profiles"],
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze video with Marlin-2B VLM")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: same as video file)",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default=None,
        help="Device to use (default: auto-detect)",
    )
    args = parser.parse_args()

    analyzer = MarlinAnalyzer(
        video_path=args.video,
        output_dir=args.output_dir,
        device=args.device,
    )

    try:
        manifest = analyzer.analyze()
        print(f"\nAnalysis complete!")
        if analyzer.last_bvf_path:
            print(f"   BVF: {analyzer.last_bvf_path}")
        print(f"   Segments: {len(manifest['segments'])}")
        print(f"   Mature: {sum(1 for s in manifest['segments'] if s['risk'] == 'mature')}")
        print(f"   Safe: {sum(1 for s in manifest['segments'] if s['risk'] == 'safe')}")

        # Print segment details
        for seg in manifest["segments"]:
            dur = seg["end_time"] - seg["start_time"]
            tags_str = f" tags={seg['tags']}" if seg["tags"] else ""
            print(f"   {seg['id']}: {seg['start_time']:.1f}s - {seg['end_time']:.1f}s ({dur:.1f}s) [{seg['risk']}]{tags_str}")
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
