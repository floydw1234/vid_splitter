"""
Smart Branching Analyzer
Scans a video file, detects mature content, and generates a smart_branch.json manifest.

Architecture:
  1. Whisper → timestamped transcript with word-level timing
  2. Frame extraction → 1 frame every 5 seconds via FFmpeg
  3. Safety checker → NSFW detection on extracted frames
  4. Segment merging → combine overlapping flags into time-bounded segments
  5. Manifest output → smart_branch.json

Usage:
  python analyze.py "path/to/movie.mp4" [--model base|tiny|medium] [--threshold 0.7]
"""
import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

import torch
import ffmpeg
import whisper
from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker
from transformers import CLIPImageProcessor
from PIL import Image
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Profanity list — expand as needed
PROFANITY_LIST = [
    "fuck", "shit", "damn", "hell", "bastard", "asshole", "bitch",
    "cunt", "pussy", "dick", "cock", "nigger", "nigga", "faggot",
    "whore", "slut", "retard",
]

# Default profiles baked into every manifest
DEFAULT_PROFILES = {
    "child": {"age": 10, "gender": "any", "filters": ["nudity", "violence", "language", "fear"]},
    "teen_m": {"age": 15, "gender": "male", "filters": ["nudity", "gore"]},
    "teen_f": {"age": 15, "gender": "female", "filters": ["nudity", "violence"]},
    "adult": {"age": 18, "gender": "any", "filters": []},
}

# Tags that map to filter categories
TAG_TO_FILTER = {
    "language": "language",
    "nudity": "nudity",
    "violence": "violence",
    "gore": "gore",
    "fear": "fear",
}


class MovieAnalyzer:
    """Analyzes a video file for mature content and generates a branching manifest."""

    def __init__(
        self,
        video_path: str,
        output_dir: str | None = None,
        whisper_model: str = "base",
        nsfw_threshold: float = 0.6,
        frame_interval: int = 5,
    ):
        self.video_path = Path(video_path).resolve()
        self.output_dir = Path(output_dir) if output_dir else self.video_path.parent
        self.whisper_model_name = whisper_model
        self.nsfw_threshold = nsfw_threshold
        self.frame_interval = frame_interval  # seconds between frame samples

        # --- Load models (cached) ---
        logger.info(f"Loading Whisper model: {whisper_model}")
        self.whisper_model = whisper.load_model(whisper_model)

        logger.info("Loading NSFW safety checker...")
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self.safety_checker = StableDiffusionSafetyChecker.from_pretrained(
            "CompVis/stable-diffusion-safety-checker"
        ).to(self._device)
        self.feature_extractor = CLIPImageProcessor.from_pretrained(
            "CompVis/stable-diffusion-safety-checker"
        )

        logger.info("Models loaded. Ready to analyze.")

    def analyze(self) -> dict:
        """Run the full analysis pipeline and return the manifest dict."""
        logger.info(f"Analyzing: {self.video_path}")

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        # 1. Get video metadata
        duration = self._get_duration()
        logger.info(f"Video duration: {duration:.1f}s")

        # 2. Transcribe audio with word-level timestamps
        logger.info("Transcribing audio...")
        transcript_data = self._transcribe()

        # 3. Extract frames and run NSFW detection
        logger.info(f"Extracting frames (every {self.frame_interval}s)...")
        frame_results = self._extract_and_classify_frames(duration)

        # 4. Build time-binned detections
        detections = self._build_detections(transcript_data, frame_results, duration)

        # 5. Merge overlapping detections into segments
        segments = self._merge_segments(detections, duration)

        # 6. Generate manifest
        manifest = self._build_manifest(segments, duration)

        # 7. Save manifest
        output_json = self._save_manifest(manifest)
        logger.info(f"Manifest saved to: {output_json}")

        return manifest

    # ─── Step 1: Duration ───────────────────────────────────────────────

    def _get_duration(self) -> float:
        """Get video duration via FFprobe."""
        probe = ffmpeg.probe(str(self.video_path))
        return float(probe["format"]["duration"])

    # ─── Step 2: Transcription ──────────────────────────────────────────

    def _transcribe(self) -> dict:
        """Run Whisper transcription. Returns full result dict with word-level timestamps."""
        # Whisper returns word-level timestamps when the model supports it (base+)
        result = self.whisper_model.transcribe(
            str(self.video_path),
            word_timestamps=True,  # Enable word-level timing
            verbose=False,
        )
        return result

    # ─── Step 3: Frame Extraction + NSFW ────────────────────────────────

    def _extract_and_classify_frames(self, duration: float) -> list[dict]:
        """Extract one frame every `frame_interval` seconds and classify for NSFW."""
        frames_dir = self.output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        results = []
        num_frames = int(duration / self.frame_interval)

        for i in range(num_frames):
            start_time = i * self.frame_interval
            frame_path = frames_dir / f"frame_{i:04d}.jpg"

            # Extract single frame at exact timestamp
            try:
                (
                    ffmpeg
                    .input(str(self.video_path), ss=start_time)
                    .output(str(frame_path), vframes=1, format="image2")
                    .overwrite_output()
                    .run(quiet=True, capture_stdout=True, capture_stderr=True)
                )
            except ffmpeg.Error as e:
                logger.warning(f"Failed to extract frame at {start_time}s: {e.stderr.decode()}")
                continue

            if not frame_path.exists():
                continue

            # Classify with safety checker
            nsfw_score = self._classify_frame(frame_path)

            if nsfw_score >= self.nsfw_threshold:
                results.append({
                    "time": start_time,
                    "type": "nudity",  # Safety checker detects nudity/pornography
                    "score": float(nsfw_score),
                })

        logger.info(f"Extracted {num_frames} frames, flagged {len(results)} as NSFW")
        return results

    def _classify_frame(self, frame_path: Path) -> float:
        """Run a single image through the safety checker. Returns 1.0 if NSFW, else 0.0."""
        image = Image.open(frame_path).convert("RGB")
        image_array = np.array(image)

        safety_input = self.feature_extractor(
            images=image, return_tensors="pt"
        ).to(self._device)

        # forward() returns (images, has_nsfw_concept: List[bool])
        with torch.no_grad():
            _, has_nsfw = self.safety_checker(
                clip_input=safety_input.pixel_values,
                images=[image_array],
            )

        return 1.0 if has_nsfw[0] else 0.0

    # ─── Step 4: Build Detections ───────────────────────────────────────

    def _build_detections(
        self,
        transcript_data: dict,
        frame_results: list[dict],
        duration: float,
    ) -> list[dict]:
        """
        Combine Whisper word timestamps and frame NSFW scores into a unified
        list of time-binned detections with tags.
        """
        detections = []

        # --- Audio: map words to time bins ---
        segments_data = transcript_data.get("segments", [])
        words = []
        for seg in segments_data:
            for word_data in seg.get("words", []):
                words.append({
                    "word": word_data["word"].strip().lower(),
                    "start": word_data["start"],
                    "end": word_data["end"],
                })

        # Bin words into frame-interval buckets
        num_buckets = int(duration / self.frame_interval)
        for bucket_idx in range(num_buckets):
            bucket_start = bucket_idx * self.frame_interval
            bucket_end = bucket_start + self.frame_interval

            bucket_words = [
                w for w in words if bucket_start <= w["start"] < bucket_end
            ]

            tags = set()
            for w in bucket_words:
                word_clean = w["word"].strip("'\"")
                if word_clean in PROFANITY_LIST:
                    tags.add("language")

            if tags:
                detections.append({
                    "time": bucket_start,
                    "type": "audio",
                    "tags": list(tags),
                    "score": 1.0,
                })

        # --- Visual: add frame NSFW detections ---
        for frame in frame_results:
            detections.append(frame)

        # Sort by time
        detections.sort(key=lambda d: d["time"])
        return detections

    # ─── Step 5: Merge into Segments ────────────────────────────────────

    def _merge_segments(
        self,
        detections: list[dict],
        duration: float,
    ) -> list[dict]:
        """
        Merge overlapping detections into contiguous segments.
        Each segment has a start/end time and a set of tags.
        """
        if not detections:
            # No flags at all — one safe segment covering the whole video
            return [{
                "id": "seg_001",
                "start_time": 0,
                "end_time": duration,
                "tags": [],
                "risk": "safe",
                "action": "play",
            }]

        segments = []
        current_tags = set()
        current_start = detections[0]["time"]

        for i, det in enumerate(detections):
            current_tags.update(det["tags"]) if det["type"] == "audio" else current_tags.add("nudity")

            # Check if next detection is within frame_interval (contiguous)
            is_last = i == len(detections) - 1
            next_time = detections[i + 1]["time"] if not is_last else duration

            if is_last or (next_time - det["time"]) > self.frame_interval:
                # End of a contiguous group
                current_end = min(det["time"] + self.frame_interval, duration)

                tags = sorted(current_tags)
                risk = "mature" if tags else "safe"

                segments.append({
                    "id": f"seg_{len(segments)+1:03d}",
                    "start_time": round(current_start, 2),
                    "end_time": round(current_end, 2),
                    "tags": tags,
                    "risk": risk,
                    "action": "swap" if risk == "mature" else "play",
                })

                current_tags = set()
                current_start = next_time

        # Ensure we cover the full duration — fill any gaps
        segments = self._fill_gaps(segments, duration)

        return segments

    def _fill_gaps(self, segments: list[dict], duration: float) -> list[dict]:
        """Fill time gaps between segments with safe segments."""
        if not segments:
            return [{
                "id": "seg_001",
                "start_time": 0,
                "end_time": duration,
                "tags": [],
                "risk": "safe",
                "action": "play",
            }]

        filled = []
        prev_end = 0

        for seg in segments:
            if seg["start_time"] > prev_end:
                # Gap before this segment
                filled.append({
                    "id": "",  # renumbered below
                    "start_time": round(prev_end, 2),
                    "end_time": round(seg["start_time"], 2),
                    "tags": [],
                    "risk": "safe",
                    "action": "play",
                })
            filled.append(seg)
            prev_end = max(prev_end, seg["end_time"])

        # Fill gap after last segment
        if prev_end < duration:
            filled.append({
                "id": "",
                "start_time": round(prev_end, 2),
                "end_time": round(duration, 2),
                "tags": [],
                "risk": "safe",
                "action": "play",
            })

        # Renumber all segments sequentially to avoid ID collisions from gap insertion
        for i, seg in enumerate(filled):
            seg["id"] = f"seg_{i+1:03d}"

        return filled

    # ─── Step 6: Build Manifest ─────────────────────────────────────────

    def _build_manifest(self, segments: list[dict], duration: float) -> dict:
        return {
            "movie_id": self.video_path.stem,
            "movie_path": str(self.video_path),
            "duration_seconds": round(duration, 2),
            "analyzed_at": datetime.utcnow().isoformat(),
            "profiles": DEFAULT_PROFILES,
            "segments": segments,
        }

    # ─── Step 7: Save ───────────────────────────────────────────────────

    def _save_manifest(self, manifest: dict) -> Path:
        output_json = self.output_dir / f"{self.video_path.stem}_branch.json"
        with open(output_json, "w") as f:
            json.dump(manifest, f, indent=2)
        return output_json


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze video for mature content")
    parser.add_argument("video", help="Path to video file (.mp4, .mkv, etc.)")
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: base)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="NSFW confidence threshold (default: 0.6)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Frame extraction interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: same as video file)",
    )

    args = parser.parse_args()

    analyzer = MovieAnalyzer(
        video_path=args.video,
        output_dir=args.output_dir,
        whisper_model=args.model,
        nsfw_threshold=args.threshold,
        frame_interval=args.interval,
    )

    try:
        manifest = analyzer.analyze()
        print(f"\n✅ Analysis complete!")
        print(f"   Segments: {len(manifest['segments'])}")
        print(f"   Mature: {sum(1 for s in manifest['segments'] if s['risk'] == 'mature')}")
        print(f"   Safe: {sum(1 for s in manifest['segments'] if s['risk'] == 'safe')}")
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
