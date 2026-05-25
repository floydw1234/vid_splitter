"""
Smart Branching Analyzer
Scans a video file, detects mature content, and generates a .bvf container.

Architecture:
  1. Whisper → timestamped transcript with word-level timing
  2. Frame extraction → 1 frame every 5 seconds via FFmpeg
  3. Safety checker → NSFW detection on extracted frames
  4. Segment merging → combine overlapping flags into time-bounded segments
  5. BVF output → movie.bvf

Usage:
  python analyze.py "path/to/movie.mp4" [--model base|tiny|medium] [--threshold 0.7]
"""
import sys
import argparse
import logging
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from PIL import Image
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vid_splitter.bvf_muxer import BvfMuxer

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
        cartoon_threshold: float = 0.8,
        frame_interval: int = 5,
        load_models: bool = True,
    ):
        self.video_path = Path(video_path).resolve()
        self.output_dir = Path(output_dir) if output_dir else self.video_path.parent
        self.whisper_model_name = whisper_model
        self.nsfw_threshold = nsfw_threshold
        self.cartoon_threshold = cartoon_threshold
        self.frame_interval = frame_interval  # seconds between frame samples
        self.last_bvf_path: Path | None = None

        if load_models:
            self._load_models()

    def _load_models(self) -> None:
        """Load ML models lazily so demo/manual analyzer modes stay lightweight."""
        import torch
        import whisper
        from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker
        from transformers import CLIPImageProcessor, AutoModelForImageClassification, AutoImageProcessor

        logger.info(f"Loading Whisper model: {self.whisper_model_name}")
        self.whisper_model = whisper.load_model(self.whisper_model_name)

        logger.info("Loading NSFW safety checker...")
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self.safety_checker = StableDiffusionSafetyChecker.from_pretrained(
            "CompVis/stable-diffusion-safety-checker"
        ).to(self._device)
        self.feature_extractor = CLIPImageProcessor.from_pretrained(
            "CompVis/stable-diffusion-safety-checker"
        )

        logger.info("Loading Falconsai NSFW detector (ViT)...")
        self.nsfw_model = AutoModelForImageClassification.from_pretrained(
            "Falconsai/nsfw_image_detection"
        ).to(self._device)
        self.nsfw_processor = AutoImageProcessor.from_pretrained(
            "Falconsai/nsfw_image_detection"
        )

        logger.info("Loading Skin Detector (HSV-based)...")
        from analyzer.skin_detector import SkinDetector
        self.skin_detector = SkinDetector()

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
        self._transcript_data = transcript_data  # Store for topic classification

        # 3. Extract frames and run NSFW detection
        logger.info(f"Extracting frames (every {self.frame_interval}s)...")
        frame_results = self._extract_and_classify_frames(duration)

        # 4. Build time-binned detections
        detections = self._build_detections(transcript_data, frame_results, duration)

        # 5. Merge overlapping detections into segments
        segments = self._merge_segments(detections, duration)

        # 5b. Classify segments for topics using LLM
        logger.info("Classifying segments for topics with LLM...")
        segments = self._classify_topics(segments)

        # 6. Generate manifest
        manifest = self._build_manifest(segments, duration)

        self._attach_media_packets(manifest["segments"])

        # 7. Save BVF container
        output_bvf = self._save_bvf(manifest)
        self.last_bvf_path = output_bvf
        logger.info(f"BVF saved to: {output_bvf}")

        return manifest

    # ─── Step 1: Duration ───────────────────────────────────────────────

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

    # ─── Step 2: Transcription ──────────────────────────────────────────

    def _transcribe(self) -> dict:
        """Run Whisper transcription. Returns full result dict with word-level timestamps.
        
        Also computes a 'silence_ratio' field (0.0 = all speech, 1.0 = all silence)
        based on gaps between spoken words.
        """
        # Whisper returns word-level timestamps when the model supports it (base+)
        result = self.whisper_model.transcribe(
            str(self.video_path),
            word_timestamps=True,  # Enable word-level timing
            verbose=False,
        )
        
        # Compute silence ratio from transcript words
        words = []
        for seg in result.get("segments", []):
            for word_data in seg.get("words", []):
                words.append({
                    "start": word_data["start"],
                    "end": word_data["end"],
                })
        
        if words:
            total_speech = sum(w["end"] - w["start"] for w in words)
            total_duration = words[-1]["end"] - words[0]["start"] if words else 0
            silence_ratio = max(0.0, min(1.0, 1.0 - (total_speech / total_duration))) if total_duration > 0 else 0.0
        else:
            silence_ratio = 1.0  # No words = complete silence
        
        result["silence_ratio"] = silence_ratio
        return result

    # ─── Step 3: Frame Extraction + NSFW ────────────────────────────────

    def _extract_and_classify_frames(self, duration: float) -> list[dict]:
        """Extract one frame every `frame_interval` seconds and classify for NSFW.

        When a frame is flagged, binary-search backward and forward to find the
        exact boundaries where content becomes bad/safe.
        """
        frames_dir = self.output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        results = []
        num_frames = max(1, int(np.ceil(duration / self.frame_interval)))

        for i in range(num_frames):
            start_time = i * self.frame_interval
            frame_path = frames_dir / f"frame_{i:04d}.jpg"

            # Extract single frame at exact timestamp
            try:
                (
                    self._extract_frame(start_time, frame_path)
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
                logger.warning(f"Failed to extract frame at {start_time}s: {stderr}")
                continue

            if not frame_path.exists():
                continue

            # Classify with safety checker → (confidence, has_nsfw_concept)
            nsfw_score, has_nsfw = self._classify_frame(frame_path)

            if has_nsfw:
                # Detect if frame is cartoon/anime
                is_cartoon = self._detect_cartoon(frame_path)

                # Use appropriate threshold based on media type
                threshold = self.cartoon_threshold if is_cartoon else self.nsfw_threshold

                # Apply threshold before forwarding visual detections downstream.
                score = float(nsfw_score)
                if score < threshold:
                    continue

                # Store media type hint for downstream use
                media_type = "cartoon" if is_cartoon else "live_action"

                # Binary-search to find exact boundaries
                bad_start = self._binary_search_boundary(
                    start_time, duration, backward=True,
                    is_cartoon=is_cartoon, threshold=threshold,
                    frames_dir=frames_dir,
                )
                bad_end = self._binary_search_boundary(
                    start_time, duration, backward=False,
                    is_cartoon=is_cartoon, threshold=threshold,
                    frames_dir=frames_dir,
                )

                logger.info(
                    f"  Refined bad segment: {bad_start:.2f}s - {bad_end:.2f}s "
                    f"(original sample at {start_time}s, span={bad_end - bad_start:.2f}s)"
                )

                results.append({
                    "time": bad_start,
                    "type": "nudity",
                    "score": score,
                    "media_type": media_type,
                    "is_cartoon": is_cartoon,
                    "bad_start": bad_start,
                    "bad_end": bad_end,
                })

        logger.info(f"Extracted {num_frames} frames, flagged {len(results)} as NSFW")
        return results

    def _binary_search_boundary(
        self,
        known_bad_time: float,
        duration: float,
        backward: bool,
        is_cartoon: bool,
        threshold: float,
        frames_dir: Path,
        precision: float = 0.1,
    ) -> float:
        """Binary-search for the exact boundary where content becomes bad/safe.

        Starts from a known-bad frame and searches backward (to find start of bad
        segment) or forward (to find end of bad segment).

        Args:
            known_bad_time: Time of the frame we know is bad.
            duration: Total video duration.
            backward: True to search backward (find start), False to search forward (find end).
            is_cartoon: Whether the content is cartoon/anime.
            threshold: NSFW confidence threshold.
            frames_dir: Directory for cached frames.
            precision: Minimum step size to stop searching (default 0.1s = 100ms).

        Returns:
            The boundary time in seconds.
        """
        if backward:
            safe_bound = 0.0
            bad_bound = known_bad_time
        else:
            bad_bound = known_bad_time
            safe_bound = duration

        while (bad_bound - safe_bound) > precision:
            probe_time = (safe_bound + bad_bound) / 2.0
            probe_time = max(0.0, min(probe_time, duration))

            frame_path = frames_dir / f"refine_{probe_time:.3f}.jpg"
            try:
                self._extract_frame(probe_time, frame_path)
            except subprocess.CalledProcessError:
                break

            if not frame_path.exists():
                break

            nsfw_score, has_nsfw = self._classify_frame(frame_path)
            probe_score = float(nsfw_score) if has_nsfw else 0.0
            probe_is_bad = has_nsfw and probe_score >= threshold

            if backward:
                # Searching backward: find where safe → bad transition happens
                if probe_is_bad:
                    bad_bound = probe_time
                else:
                    safe_bound = probe_time
            else:
                # Searching forward: find where bad → safe transition happens
                if probe_is_bad:
                    bad_bound = probe_time
                else:
                    safe_bound = probe_time

        # Backward: return first bad frame (start of bad segment)
        # Forward: return first safe frame after bad segment (end of bad segment)
        return round(bad_bound if backward else safe_bound, 2)


    def _extract_frame(self, start_time: float, frame_path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss", str(start_time),
                "-i", str(self.video_path),
                "-frames:v", "1",
                "-f", "image2",
                str(frame_path),
            ],
            check=True,
            capture_output=True,
        )

    def _classify_frame(self, frame_path: Path) -> tuple[float, bool]:
        """Run a single image through three NSFW checkers. Returns (confidence, has_nsfw_concept).

        Uses three models for maximum coverage:
        1. Stable Diffusion Safety Checker (CLIP-based)
        2. Falconsai ViT NSFW detector (ViT-based, 98% accuracy)
        3. SAM 2 Skin Detector (segmentation + skin tone analysis)

        Returns True if any model detects NSFW. Confidence is the max of all.
        """
        image = Image.open(frame_path).convert("RGB")
        image_array = np.array(image)

        import torch

        # --- Checker 1: Stable Diffusion Safety Checker ---
        safety_input = self.feature_extractor(
            images=image, return_tensors="pt"
        ).to(self._device)

        with torch.no_grad():
            feature_values, has_nsfw = self.safety_checker(
                clip_input=safety_input.pixel_values,
                images=[image_array],
            )

        sd_confidence = 0.0
        if has_nsfw[0]:
            if isinstance(feature_values, list):
                tensor_val = feature_values[0] if feature_values else None
            else:
                tensor_val = feature_values
            if tensor_val is not None and hasattr(tensor_val, "numel") and tensor_val.numel() > 0:
                sd_confidence = float(tensor_val.abs().max().item())
                sd_confidence = max(0.0, min(1.0, (sd_confidence + 1.0) / 2.0))
            else:
                sd_confidence = 0.5

        # --- Checker 2: Falconsai ViT NSFW detector ---
        falcon_confidence = 0.0
        try:
            falcon_inputs = self.nsfw_processor(images=image, return_tensors="pt").to(self._device)
            with torch.no_grad():
                falcon_outputs = self.nsfw_model(**falcon_inputs)
                falcon_probs = torch.softmax(falcon_outputs.logits, dim=-1)
            # Class 1 = "nsfw", Class 0 = "normal"
            falcon_confidence = float(falcon_probs[0][1].item())
        except Exception as e:
            logger.warning(f"Falconsai checker failed: {e}")

        # --- Checker 3: Skin Detector (HSV-based) ---
        skin_confidence = 0.0
        skin_has_nsfw = False
        try:
            skin_confidence, skin_has_nsfw = self.skin_detector.analyze_frame(frame_path)
        except Exception as e:
            logger.warning(f"Skin detector failed: {e}")

        # Combine: use max confidence, flag if any detects NSFW
        combined_confidence = max(sd_confidence, falcon_confidence, skin_confidence)
        # Skin detector is authoritative for older films where other classifiers fail
        has_nsfw_combined = has_nsfw[0] or falcon_confidence > 0.3 or skin_has_nsfw

        return combined_confidence, has_nsfw_combined

    def _detect_cartoon(self, frame_path: Path) -> bool:
        """Heuristic to detect if a frame is cartoon/anime vs live-action.
        
        Uses two signals:
        1. Color saturation distribution: cartoons tend to have higher peak saturation
           and less color diversity (more uniform colors)
        2. Edge density: cartoons have sharper, more uniform edges with less texture
        
        Returns True if likely cartoon/anime, False otherwise.
        """
        image = Image.open(frame_path).convert("RGB")
        image_np = np.array(image)
        
        # Convert to HSV for saturation analysis
        try:
            from PIL import ImageChops
            import cv2
        except ImportError:
            # Fallback: if cv2 not available, use a simpler heuristic
            return self._simple_cartoon_check(image_np)
        
        hsv = cv2.cvtColor(image_np, cv2.COLOR_RGB2HSV)
        s_channel = hsv[:, :, 1]
        
        # Signal 1: Saturation analysis
        # Cartoons tend to have higher peak saturation (more intense colors)
        sat_mean = np.mean(s_channel)
        sat_std = np.std(s_channel)
        sat_hist, _ = np.histogram(s_channel, bins=10, range=(0, 255))
        # Check for saturation peaks (cartoons have concentrated saturation)
        sat_entropy = -np.sum((sat_hist / (sat_hist.sum() + 1e-6)) * np.log2((sat_hist / (sat_hist.sum() + 1e-6)) + 1e-6))
        
        # Signal 2: Edge density and uniformity
        gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / edges.size
        
        # Cartoon heuristics:
        # - High saturation peak (low std relative to mean)
        # - Moderate edge density (cartoons have edges but less texture noise)
        # - Lower color entropy (less varied colors)
        
        # Simple scoring: combine signals
        # High saturation + moderate edges = likely cartoon
        sat_score = min(1.0, sat_mean / 180.0) if sat_mean > 0 else 0
        edge_score = min(1.0, edge_density / 0.3)  # normalize to expected range
        
        # Cartoon score: high saturation, moderate edges
        cartoon_score = (sat_score * 0.6) + (edge_score * 0.4)
        
        return bool(cartoon_score > 0.5)

    def _simple_cartoon_check(self, image_np: np.ndarray) -> bool:
        """Fallback cartoon detection without cv2.
        
        Uses simple color statistics to detect cartoon-like content.
        """
        # Convert to HSV manually for basic saturation check
        if image_np.shape[2] != 3:
            return False
        
        rgb = image_np.astype(np.float32) / 255.0
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        
        max_c = np.maximum(np.maximum(r, g), b)
        min_c = np.minimum(np.minimum(r, g), b)
        sat = max_c - min_c
        max_c_safe = np.where(max_c > 0, max_c, 1.0)
        sat = sat / max_c_safe
        
        # Cartoon images tend to have higher average saturation
        avg_sat = np.mean(sat)
        
        # Also check for color banding (common in cartoons)
        sat_unique = len(np.unique(sat.flatten()))
        
        # Heuristic: high saturation + fewer unique saturation values
        return (avg_sat > 0.4) and (sat_unique < 100)

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

        # Bin words into frame-interval buckets. Include a trailing partial bucket.
        num_buckets = max(1, int(np.ceil(duration / self.frame_interval)))
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
        # Get silence ratio from transcript for audio heuristic
        silence_ratio = transcript_data.get("silence_ratio", 0.0)

        for frame in frame_results:
            detection = dict(frame)  # copy

            # Audio heuristic: if mostly silent/no dialogue, deprioritize visual-only flags
            detection["audio_silenced"] = False
            if silence_ratio > 0.7:
                detection["score"] = detection["score"] * 0.5
                detection["score"] = round(detection["score"], 4)
                detection["audio_silenced"] = True

            detections.append(detection)

        # Sort by time
        detections.sort(key=lambda d: d["time"])
        return detections

    # ─── Step 3b: LLM Topic Classification ──────────────────────────────

    def _classify_topics(self, segments: list[dict]) -> list[dict]:
        """Classify segments for topics using LLM.

        Adds 'topics' key to each segment with LLM-classified topics.
        """
        try:
            from analyzer.topic_classifier import LLMTopicClassifier
            clf = LLMTopicClassifier()

            # Build transcript segments for classification
            transcript_segs = []
            for seg in segments:
                # Extract transcript for this time range
                start = seg.get("start_time", 0)
                end = seg.get("end_time", 0)
                transcript = self._extract_transcript_for_segment(start, end)
                transcript_segs.append({
                    "transcript": transcript,
                    "start_time": start,
                    "end_time": end,
                })

            # Classify
            classified = clf.classify_segments(transcript_segs)

            # Add topics back to segments
            for seg, cls in zip(segments, classified):
                seg["topics"] = cls.get("topics", [])

        except Exception as e:
            logger.warning(f"LLM topic classification failed: {e}")

        return segments

    def _extract_transcript_for_segment(self, start: float, end: float) -> str:
        """Extract transcript text for a time range."""
        transcript = getattr(self, '_transcript_data', None)
        if not transcript:
            return ""

        words = []
        for seg in transcript.get("segments", []):
            for word_data in seg.get("words", []):
                w = word_data["word"].strip().lower()
                ws = word_data["start"]
                we = word_data["end"]
                # Include word if it overlaps with the time range
                if ws < end and we > start:
                    words.append(w)

        return " ".join(words)

    # ─── Step 5: Merge into Segments ────────────────────────────────────

    def _merge_segments(
        self,
        detections: list[dict],
        duration: float,
    ) -> list[dict]:
        """
        Merge overlapping detections into contiguous segments.
        Each segment has a start/end time and a set of tags.

        For visual detections with refined boundaries (bad_start/bad_end),
        uses those precise times. Falls back to frame_interval extension
        for audio detections and unrefined visual detections.
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
        current_end = None

        for i, det in enumerate(detections):
            current_tags.update(det["tags"]) if det["type"] == "audio" else current_tags.add("nudity")

            # Determine this detection's end time
            if "bad_end" in det:
                # Refined boundary from binary search
                seg_end = det["bad_end"]
            else:
                # Fallback: extend by frame_interval
                seg_end = min(det["time"] + self.frame_interval, duration)

            # Update current segment end
            if current_end is None:
                current_end = seg_end
            else:
                current_end = max(current_end, seg_end)

            # Check if next detection is within frame_interval (contiguous)
            is_last = i == len(detections) - 1
            next_time = detections[i + 1]["time"] if not is_last else duration

            if is_last or (next_time - det["time"]) > self.frame_interval:
                # End of a contiguous group
                current_end = min(current_end or seg_end, duration)

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
                current_end = None

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
        # Include topics in manifest for dynamic profile resolution
        manifest_segments = []
        for seg in segments:
            start_time = seg.get("start_time", 0)
            end_time = seg.get("end_time", 0)
            manifest_seg = {
                "id": seg["id"],
                "start_time": start_time,
                "end_time": end_time,
                "start_ms": int(start_time * 1000),
                "end_ms": int(end_time * 1000),
                "tags": seg.get("tags", []),
                "topics": seg.get("topics", []),
                "risk": seg.get("risk", "safe"),
                "profiles": seg.get("profiles", {}),
            }
            manifest_segments.append(manifest_seg)

        # Log segment summary
        logger.info(f"Manifest: {len(manifest_segments)} segments")
        for seg in manifest_segments:
            logger.info(
                f"  {seg['id']:8s} | {seg['risk']:8s} | "
                f"{seg['start_time']:7.1f}s - {seg['end_time']:7.1f}s | "
                f"tags={seg.get('tags', [])} | topics={seg.get('topics', [])}"
            )

        return {
            "movie_id": self.video_path.stem,
            "movie_path": str(self.video_path),
            "duration_seconds": round(duration, 2),
            "analyzed_at": datetime.utcnow().isoformat(),
            "profiles": DEFAULT_PROFILES,
            "segments": manifest_segments,
        }


    def analyze_demo_branch(self) -> dict:
        """Create a deterministic branchable BVF without ML model dependencies.

        This is intended for local end-to-end verification. It probes the input
        video, marks the middle third as mature language content, embeds remuxed
        media bytes for each segment, and writes the normal BVF container.
        """
        logger.info(f"Running demo branch analysis: {self.video_path}")
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        duration = self._get_duration()
        one_third = duration / 3.0
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0,
                "end_time": round(one_third, 2),
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
            {
                "id": "seg_002",
                "start_time": round(one_third, 2),
                "end_time": round(one_third * 2, 2),
                "tags": ["language"],
                "risk": "mature",
                "action": "skip",
            },
            {
                "id": "seg_003",
                "start_time": round(one_third * 2, 2),
                "end_time": round(duration, 2),
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
        ]
        segments = [s for s in segments if s["end_time"] > s["start_time"]]
        for i, seg in enumerate(segments):
            seg["id"] = f"seg_{i + 1:03d}"

        manifest = self._build_manifest(segments, duration)
        self._attach_media_packets(manifest["segments"])
        output_bvf = self._save_bvf(manifest)
        self.last_bvf_path = output_bvf
        logger.info(f"BVF saved to: {output_bvf}")
        return manifest

    def _attach_media_packets(self, segments: list[dict]) -> None:
        """Embed MPEG-TS media payloads for every segment in-place."""
        with tempfile.TemporaryDirectory(prefix="bvf_analyzer_segments_") as tmp:
            tmp_dir = Path(tmp)
            for seg in segments:
                segment_path = tmp_dir / f"{seg['id']}.ts"
                self._remux_segment(seg["start_time"], seg["end_time"], segment_path)
                seg["video_packets"] = [
                    {
                        "pts_ms": int(seg["start_time"] * 1000),
                        "data": segment_path.read_bytes(),
                    }
                ]
                seg["audio_packets"] = []

    def _remux_segment(self, start_time: float, end_time: float, output_path: Path) -> None:
        duration = max(0.001, end_time - start_time)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
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

    # ─── Step 7: Save ───────────────────────────────────────────────────

    def _save_bvf(self, manifest: dict) -> Path:
        """Write analyzer output into a BVF container."""
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
        "--cartoon-threshold",
        type=float,
        default=0.8,
        help="Cartoon/anime NSFW confidence threshold (default: 0.8, higher to reduce false positives)",
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
    parser.add_argument(
        "--demo-branch",
        action="store_true",
        help="Create a deterministic safe/mature/safe BVF without loading ML models",
    )
    args = parser.parse_args()

    analyzer = MovieAnalyzer(
        video_path=args.video,
        output_dir=args.output_dir,
        whisper_model=args.model,
        nsfw_threshold=args.threshold,
        cartoon_threshold=args.cartoon_threshold,
        frame_interval=args.interval,
        load_models=not args.demo_branch,
    )

    try:
        manifest = analyzer.analyze_demo_branch() if args.demo_branch else analyzer.analyze()
        print(f"\n✅ Analysis complete!")
        if analyzer.last_bvf_path:
            print(f"   BVF: {analyzer.last_bvf_path}")
        print(f"   Segments: {len(manifest['segments'])}")
        print(f"   Mature: {sum(1 for s in manifest['segments'] if s['risk'] == 'mature')}")
        print(f"   Safe: {sum(1 for s in manifest['segments'] if s['risk'] == 'safe')}")
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
