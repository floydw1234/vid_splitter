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
  python analyze.py "path/to/movie.mp4" [--model base|tiny|medium] [--threshold 0.75]
"""
import sys
import argparse
import hashlib
import json
import logging
import subprocess
import tempfile
from bisect import bisect_left
from pathlib import Path
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageEnhance, ImageOps
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from vid_splitter.bvf_muxer import BvfMuxer
from analyzer.filler import pick_filler_window

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FALCON_TRIGGER_GATE = 0.5
DARK_FRAME_LUMA_THRESHOLD = 70.0
DARK_SCENE_SKIN_GATE = 0.75
BRIGHTNESS_RESCUE_GAIN = 1.75
CONTRAST_RESCUE_GAIN = 1.15

# This module coordinates analysis steps across the pipeline.
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
            "nudity": "skip",
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
            "nudity": "skip",
            "gore": "skip",
            "profanity": "mute",
        },
    },
    "teen_f": {
        "name": "Teen Female (13-17)",
        "description": "Blocks nudity and violence",
        "filters": {
            "nudity": "skip",
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

class MovieAnalyzer:
    """Analyzes a video file for mature content and generates a branching manifest."""

    def __init__(
        self,
        video_path: str,
        output_dir: str | None = None,
        whisper_model: str = "base",
        nsfw_threshold: float = 0.75,
        cartoon_threshold: float = 0.8,
        frame_interval: int = 5,
        scan_interval: float | None = None,
        candidate_threshold: float = 0.25,
        dense_rescan_fps: float = 2.0,
        dense_window_padding: float = 5.0,
        min_positive_frames: int = 2,
        debug_contact_sheet: str | Path | None = None,
        load_models: bool = True,
        demo_filler_video: str | None = None,
        demo_filler_start: float = 0.0,
        demo_filler_duration: float | None = None,
    ):
        self.video_path = Path(video_path).resolve()
        self.output_dir = Path(output_dir) if output_dir else self.video_path.parent
        self.whisper_model_name = whisper_model
        self.nsfw_threshold = nsfw_threshold
        self.cartoon_threshold = cartoon_threshold
        self.frame_interval = float(frame_interval)  # output segment bucket cadence
        self.scan_interval = float(scan_interval) if scan_interval is not None else float(frame_interval)
        self.candidate_threshold = round(float(candidate_threshold), 4)
        self.dense_rescan_fps = max(0.001, float(dense_rescan_fps))
        self.dense_window_padding = max(0.0, float(dense_window_padding))
        self.min_positive_frames = max(1, int(min_positive_frames))
        self.debug_contact_sheet = Path(debug_contact_sheet).expanduser() if debug_contact_sheet else None
        self.last_bvf_path: Path | None = None
        self.demo_filler_video = Path(demo_filler_video).resolve() if demo_filler_video else None
        self.demo_filler_start = max(0.0, demo_filler_start)
        self.demo_filler_duration = demo_filler_duration
        self.goldylocks_filler_video = (REPO_ROOT / "videos" / "goldylocks.mp4").resolve()
        self.skin_detector = None

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

        self._get_skin_detector()

        logger.info("Models loaded. Ready to analyze.")

    def _get_skin_detector(self):
        if self.skin_detector is None:
            logger.info("Loading Skin Detector (HSV-based)...")
            from analyzer.skin_detector import SkinDetector

            self.skin_detector = SkinDetector()
        return self.skin_detector

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
        logger.info(
            "Scanning frames (broad every %.2fs, buckets every %.2fs)...",
            self.scan_interval,
            self.frame_interval,
        )
        frame_results = self._extract_and_classify_frames(duration)

        # 4. Build time-binned detections
        detections = self._build_detections(transcript_data, frame_results, duration)

        # 5. Merge overlapping detections into segments
        segments = self._merge_segments(detections, duration)

        # 5b. Classify segments for topics using LLM
        logger.info("Classifying segments for topics with LLM...")
        segments = self._classify_topics(segments)

        # Keep analyzer output on the requested frame_interval cadence. Segment
        # media is re-encoded below, so cuts no longer need to be snapped to
        # sparse source keyframes.
        segments = self._attach_goldylocks_fillers(segments)

        # 6. Generate manifest
        manifest = self._build_manifest(segments, duration)

        self._attach_media_assets(manifest["segments"])

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
        """Run a broad scan, then densely rescan only candidate windows."""
        frames_dir = self.output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        broad_results = []
        for i, timestamp in enumerate(self._build_scan_timestamps(0.0, duration, self.scan_interval)):
            scan_result = self._scan_frame_at_timestamp(
                timestamp=timestamp,
                duration=duration,
                phase="broad",
                frames_dir=frames_dir,
                label=f"{i:04d}",
            )
            if scan_result is not None:
                broad_results.append(scan_result)

        candidate_frames = [
            result for result in broad_results
            if self._is_candidate_frame(result["classification"])
        ]
        candidate_windows = self._merge_candidate_windows(candidate_frames, duration)
        logger.info(
            "Broad scan sampled %d frame(s), found %d candidate frame(s), and merged them into %d window(s).",
            len(broad_results),
            len(candidate_frames),
            len(candidate_windows),
        )

        dense_results = []
        detections = []
        for window_index, window in enumerate(candidate_windows):
            window_results = self._scan_dense_window(
                window=window,
                window_index=window_index,
                duration=duration,
                frames_dir=frames_dir,
            )
            dense_results.extend(window_results)
            detection = self._build_dense_window_detection(
                window=window,
                dense_results=window_results,
                duration=duration,
                frames_dir=frames_dir,
            )
            if detection is not None:
                detections.append(detection)

        if self.debug_contact_sheet:
            self._export_debug_contact_sheet(broad_results + dense_results)

        logger.info(
            "Dense scan sampled %d frame(s) across %d candidate window(s) and confirmed %d mature detection(s).",
            len(dense_results),
            len(candidate_windows),
            len(detections),
        )
        return detections

    def _build_scan_timestamps(
        self,
        start_time: float,
        end_time: float,
        interval: float,
        *,
        include_end: bool = False,
    ) -> list[float]:
        start_time = max(0.0, float(start_time))
        end_time = max(start_time, float(end_time))
        interval = max(0.001, float(interval))

        timestamps = []
        current = start_time
        while current < end_time - 1e-6:
            timestamps.append(round(current, 3))
            current += interval

        if not timestamps:
            timestamps.append(round(start_time, 3))

        if include_end and end_time > timestamps[-1] + 1e-6:
            timestamps.append(round(end_time, 3))

        return timestamps

    @staticmethod
    def _clamp_sample_time(timestamp: float, duration: float) -> float:
        if duration <= 0:
            return 0.0
        return round(max(0.0, min(float(timestamp), max(0.0, duration - 0.001))), 3)

    def _scan_frame_at_timestamp(
        self,
        *,
        timestamp: float,
        duration: float,
        phase: str,
        frames_dir: Path,
        label: str,
    ) -> dict | None:
        sample_time = self._clamp_sample_time(timestamp, duration)
        frame_path = frames_dir / f"{phase}_{label}_{sample_time:010.3f}.jpg"

        try:
            self._extract_frame(sample_time, frame_path)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            logger.warning("Failed to extract %s frame at %.2fs: %s", phase, sample_time, stderr)
            return None

        if not frame_path.exists():
            return None

        is_cartoon = self._detect_cartoon(frame_path)
        media_type = "cartoon" if is_cartoon else "live_action"
        threshold = self.cartoon_threshold if is_cartoon else self.nsfw_threshold
        classification = self._classify_frame_details(frame_path, threshold=threshold)
        self._log_nudity_detection(
            timestamp=sample_time,
            classification=classification,
            media_type=media_type,
            is_cartoon=is_cartoon,
            phase=phase,
        )
        return {
            "time": sample_time,
            "requested_time": round(float(timestamp), 3),
            "phase": phase,
            "frame_path": str(frame_path),
            "media_type": media_type,
            "is_cartoon": is_cartoon,
            "classification": classification,
        }

    def _is_candidate_frame(self, classification: dict) -> bool:
        return float(classification.get("score", 0.0)) >= self.candidate_threshold

    def _merge_candidate_windows(
        self,
        candidate_frames: list[dict],
        duration: float,
    ) -> list[tuple[float, float]]:
        if not candidate_frames:
            return []

        windows = []
        for frame in sorted(candidate_frames, key=lambda item: float(item.get("time", 0.0))):
            timestamp = float(frame.get("time", 0.0))
            start_time = max(0.0, timestamp - self.dense_window_padding)
            end_time = min(float(duration), timestamp + self.dense_window_padding)
            if end_time <= start_time:
                continue

            if not windows or start_time > windows[-1][1] + 1e-6:
                windows.append([start_time, end_time])
            else:
                windows[-1][1] = max(windows[-1][1], end_time)

        return [(round(start, 2), round(end, 2)) for start, end in windows]

    def _scan_dense_window(
        self,
        *,
        window: tuple[float, float],
        window_index: int,
        duration: float,
        frames_dir: Path,
    ) -> list[dict]:
        step = 1.0 / self.dense_rescan_fps
        results = []
        for i, timestamp in enumerate(
            self._build_scan_timestamps(window[0], window[1], step, include_end=True)
        ):
            scan_result = self._scan_frame_at_timestamp(
                timestamp=timestamp,
                duration=duration,
                phase="dense",
                frames_dir=frames_dir,
                label=f"w{window_index:03d}_{i:04d}",
            )
            if scan_result is not None:
                results.append(scan_result)
        return results

    def _build_dense_window_detection(
        self,
        *,
        window: tuple[float, float],
        dense_results: list[dict],
        duration: float,
        frames_dir: Path,
    ) -> dict | None:
        positive_results = [
            result for result in dense_results
            if result["classification"]["threshold_passed"]
        ]
        if len(positive_results) < self.min_positive_frames:
            if dense_results:
                logger.info(
                    "Dense window %.2fs-%.2fs rejected with %d/%d final-pass frame(s).",
                    window[0],
                    window[1],
                    len(positive_results),
                    self.min_positive_frames,
                )
            return None

        boundary_results = [
            result for result in dense_results
            if self._counts_for_dark_scene_boundary(result["classification"])
        ]
        if not boundary_results:
            boundary_results = positive_results

        first_positive = boundary_results[0]
        last_positive = boundary_results[-1]
        anchor = max(
            positive_results,
            key=lambda result: (
                result["classification"]["score"],
                result["classification"]["sd_confidence"],
                result["classification"]["falcon_confidence"],
            ),
        )

        fallback_start, fallback_end = self._fallback_detection_span(
            window=window,
            first_positive_time=first_positive["time"],
            last_positive_time=last_positive["time"],
        )
        bad_start = self._binary_search_boundary(
            first_positive["time"],
            duration,
            backward=True,
            is_cartoon=first_positive["is_cartoon"],
            threshold=first_positive["classification"]["threshold"],
            frames_dir=frames_dir,
            search_start=window[0],
            search_end=window[1],
        )
        bad_end = self._binary_search_boundary(
            last_positive["time"],
            duration,
            backward=False,
            is_cartoon=last_positive["is_cartoon"],
            threshold=last_positive["classification"]["threshold"],
            frames_dir=frames_dir,
            search_start=window[0],
            search_end=window[1],
        )
        if bad_end <= bad_start:
            bad_start, bad_end = fallback_start, fallback_end

        logger.info(
            "Dense window %.2fs-%.2fs confirmed mature span %.2fs-%.2fs with %d final-pass frame(s).",
            window[0],
            window[1],
            bad_start,
            bad_end,
            len(positive_results),
        )

        classification = anchor["classification"]
        return {
            "time": bad_start,
            "type": "nudity",
            "score": classification["score"],
            "media_type": anchor["media_type"],
            "is_cartoon": anchor["is_cartoon"],
            "bad_start": bad_start,
            "bad_end": bad_end,
            "sd_confidence": classification["sd_confidence"],
            "falcon_confidence": classification["falcon_confidence"],
            "skin_confidence": classification["skin_confidence"],
            "skin_ratio": classification["skin_ratio"],
            "max_contour_ratio": classification["max_contour_ratio"],
            "triggered_by": list(classification["triggered_by"]),
            "threshold": classification["threshold"],
            "threshold_passed": classification["threshold_passed"],
            "phase": "dense",
            "candidate_window_start": round(window[0], 2),
            "candidate_window_end": round(window[1], 2),
            "positive_frames": len(positive_results),
            "positive_timestamps": [round(result["time"], 2) for result in positive_results],
        }

    def _fallback_detection_span(
        self,
        *,
        window: tuple[float, float],
        first_positive_time: float,
        last_positive_time: float,
    ) -> tuple[float, float]:
        half_step = 0.5 / self.dense_rescan_fps
        start_time = max(window[0], first_positive_time - half_step)
        end_time = min(window[1], last_positive_time + half_step)
        if end_time <= start_time:
            end_time = min(window[1], start_time + max(0.1, 1.0 / self.dense_rescan_fps))
        return round(start_time, 2), round(end_time, 2)

    @staticmethod
    def _counts_for_dark_scene_boundary(classification: dict) -> bool:
        if classification.get("threshold_passed"):
            return True

        return bool(
            classification.get("brightened_rescue_applied")
            and not classification.get("triggered_by")
            and float(classification.get("score", 0.0)) >= float(classification.get("threshold", 0.0))
        )

    def _binary_search_boundary(
        self,
        known_bad_time: float,
        duration: float,
        backward: bool,
        is_cartoon: bool,
        threshold: float,
        frames_dir: Path,
        precision: float = 0.1,
        search_start: float | None = None,
        search_end: float | None = None,
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
        search_start = 0.0 if search_start is None else max(0.0, float(search_start))
        search_end = duration if search_end is None else min(float(duration), float(search_end))
        known_bad_time = max(search_start, min(float(known_bad_time), search_end))

        if backward:
            safe_bound = search_start
            bad_bound = known_bad_time
        else:
            bad_bound = known_bad_time
            safe_bound = search_end

        while abs(safe_bound - bad_bound) > precision:
            probe_time = (safe_bound + bad_bound) / 2.0
            probe_time = self._clamp_sample_time(probe_time, duration)

            frame_path = frames_dir / f"refine_{probe_time:.3f}.jpg"
            try:
                self._extract_frame(probe_time, frame_path)
            except subprocess.CalledProcessError:
                break

            if not frame_path.exists():
                break

            classification = self._classify_frame_details(frame_path, threshold=threshold)
            self._log_nudity_detection(
                timestamp=probe_time,
                classification=classification,
                media_type="cartoon" if is_cartoon else "live_action",
                is_cartoon=is_cartoon,
                phase="refine",
            )
            probe_is_bad = classification["threshold_passed"]

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

    def _export_debug_contact_sheet(self, scan_results: list[dict], limit: int = 40) -> Path | None:
        if not self.debug_contact_sheet or not scan_results:
            return None

        ranked = sorted(
            scan_results,
            key=lambda result: (
                result["classification"]["score"],
                result["classification"]["threshold_passed"],
                result["classification"]["sd_confidence"],
                result["classification"]["falcon_confidence"],
            ),
            reverse=True,
        )[:limit]
        output_path = self._resolve_debug_contact_sheet_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        gutter = 12
        columns = 4
        thumb_width = 280
        thumb_height = 158
        label_height = 62
        header_height = 34
        background = (18, 18, 18)
        text_color = (240, 240, 240)
        border_color = (80, 80, 80)
        rows = int(np.ceil(len(ranked) / columns))
        canvas_width = gutter + columns * (thumb_width + gutter)
        canvas_height = header_height + gutter + rows * (thumb_height + label_height + gutter)
        canvas = Image.new("RGB", (canvas_width, canvas_height), color=background)
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (gutter, 10),
            f"{self.video_path.name} broad/dense top {len(ranked)} frames",
            fill=text_color,
        )

        for index, result in enumerate(ranked):
            row = index // columns
            column = index % columns
            left = gutter + column * (thumb_width + gutter)
            top = header_height + gutter + row * (thumb_height + label_height + gutter)
            frame_path = Path(result["frame_path"])
            try:
                image = Image.open(frame_path).convert("RGB")
                preview = ImageOps.contain(image, (thumb_width, thumb_height))
            except Exception:
                preview = Image.new("RGB", (thumb_width, thumb_height), color=(45, 45, 45))

            paste_left = left + ((thumb_width - preview.width) // 2)
            paste_top = top + ((thumb_height - preview.height) // 2)
            canvas.paste(preview, (paste_left, paste_top))
            draw.rectangle(
                (left, top, left + thumb_width - 1, top + thumb_height - 1),
                outline=border_color,
                width=1,
            )

            classification = result["classification"]
            triggered_by = ",".join(classification.get("triggered_by", [])) or "none"
            label = "\n".join(
                [
                    f"{result['time']:.2f}s [{result['phase']}] pass={classification['threshold_passed']}",
                    (
                        f"sd={classification['sd_confidence']:.2f} "
                        f"falcon={classification['falcon_confidence']:.2f} "
                        f"skin={classification['skin_confidence']:.2f}"
                    ),
                    f"triggered_by={triggered_by}",
                ]
            )
            draw.multiline_text((left, top + thumb_height + 6), label, fill=text_color, spacing=3)

        canvas.save(output_path, format="PNG")
        logger.info("Saved debug contact sheet to: %s", output_path)
        return output_path

    def _resolve_debug_contact_sheet_path(self) -> Path:
        if self.debug_contact_sheet is None:
            raise ValueError("debug contact sheet path is not configured")
        path = self.debug_contact_sheet
        if not path.is_absolute():
            try:
                path.relative_to(self.output_dir)
            except ValueError:
                path = self.output_dir / path
        if not path.suffix:
            path = path.with_suffix(".png")
        return path

    def export_skin_diagnostics(
        self,
        timestamps: list[float],
        output_dir: str | Path | None = None,
    ) -> list[Path]:
        """Save side-by-side skin-mask diagnostic PNGs for selected timestamps."""
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        diagnostics_dir = Path(output_dir) if output_dir else self.output_dir / "skin_diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)

        detector = self._get_skin_detector()
        saved_paths = []
        with tempfile.TemporaryDirectory(prefix="skin_diag_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            for index, raw_timestamp in enumerate(timestamps):
                timestamp = max(0.0, float(raw_timestamp))
                frame_path = temp_dir_path / f"skin_diag_{index:04d}.png"
                self._extract_frame(timestamp, frame_path)
                debug_data = detector.create_debug_visualization(frame_path)
                if debug_data["original_rgb"] is None:
                    raise RuntimeError(f"Failed to create skin diagnostics for {timestamp:.2f}s")

                output_path = diagnostics_dir / f"skin_diag_{timestamp:07.2f}s.png"
                panel = self._build_skin_diagnostic_panel(timestamp=timestamp, debug_data=debug_data)
                panel.save(output_path, format="PNG")
                logger.info(
                    "Saved skin diagnostic %s (skin_ratio=%.3f contour=%.3f confidence=%.3f)",
                    output_path,
                    debug_data["skin_ratio"],
                    debug_data["max_contour_ratio"],
                    debug_data["confidence"],
                )
                saved_paths.append(output_path)

        return saved_paths

    def _build_skin_diagnostic_panel(self, *, timestamp: float, debug_data: dict) -> Image.Image:
        panel_width = 360
        panel_height = 240
        gutter = 12
        header_height = 52
        label_height = 22
        background = (20, 20, 20)
        text_color = (240, 240, 240)

        panels = [
            ("Original", Image.fromarray(debug_data["original_rgb"])),
            ("HSV Skin Mask", Image.fromarray(debug_data["mask_rgb"])),
            ("Highlighted Regions", Image.fromarray(debug_data["highlighted_rgb"])),
        ]

        canvas_width = (panel_width * len(panels)) + (gutter * (len(panels) + 1))
        canvas_height = header_height + label_height + panel_height + gutter
        canvas = Image.new("RGB", (canvas_width, canvas_height), color=background)
        draw = ImageDraw.Draw(canvas)
        summary = (
            f"timestamp={timestamp:.2f}s  "
            f"skin_ratio={debug_data['skin_ratio']:.4f}  "
            f"max_contour_ratio={debug_data['max_contour_ratio']:.4f}  "
            f"skin_confidence={debug_data['confidence']:.4f}"
        )
        draw.text((gutter, 16), summary, fill=text_color)

        top = header_height + label_height
        for idx, (label, image) in enumerate(panels):
            left = gutter + idx * (panel_width + gutter)
            draw.text((left, header_height), label, fill=text_color)
            resized = ImageOps.contain(image, (panel_width, panel_height))
            paste_left = left + ((panel_width - resized.width) // 2)
            paste_top = top + ((panel_height - resized.height) // 2)
            canvas.paste(resized, (paste_left, paste_top))
            draw.rectangle(
                (left, top, left + panel_width - 1, top + panel_height - 1),
                outline=(90, 90, 90),
                width=1,
            )

        return canvas

    def _classify_frame(self, frame_path: Path) -> tuple[float, bool]:
        """Backward-compatible tuple API for frame classification.

        Returns (confidence, threshold_passed) using the live-action threshold.
        """
        classification = self._classify_frame_details(frame_path, threshold=self.nsfw_threshold)
        return classification["score"], classification["threshold_passed"]

    def _classify_frame_details(self, frame_path: Path, threshold: float) -> dict:
        """Run a single image through two decision detectors plus skin diagnostics.

        Stable Diffusion and Falcon determine the content decision. The HSV skin
        detector only contributes debug metrics and logging fields.
        """
        image = Image.open(frame_path).convert("RGB")
        sd_confidence, sd_has_nsfw, falcon_confidence = self._classify_decision_detectors_from_image(image)

        # --- Checker 3: Skin Detector (HSV-based) ---
        skin_confidence = 0.0
        skin_has_nsfw = False
        skin_ratio = 0.0
        max_contour_ratio = 0.0
        try:
            skin_result = self._get_skin_detector().analyze_frame_details(frame_path)
            skin_confidence = float(skin_result["confidence"])
            skin_has_nsfw = bool(skin_result["has_nsfw"])
            skin_ratio = float(skin_result["skin_ratio"])
            max_contour_ratio = float(skin_result["max_contour_ratio"])
        except Exception as e:
            logger.warning(f"Skin detector failed: {e}")

        mean_luma = self._mean_luma(image)
        classification = self._combine_nudity_signals(
            threshold=threshold,
            sd_confidence=sd_confidence,
            sd_has_nsfw=sd_has_nsfw,
            falcon_confidence=falcon_confidence,
            skin_confidence=skin_confidence,
            skin_has_nsfw=skin_has_nsfw,
            skin_ratio=skin_ratio,
            max_contour_ratio=max_contour_ratio,
        )
        classification["mean_luma"] = round(mean_luma, 2)

        rescue_applied = False
        rescue_triggered_by: list[str] = []
        if (
            not classification["threshold_passed"]
            and not classification["triggered_by"]
            and mean_luma <= DARK_FRAME_LUMA_THRESHOLD
            and skin_confidence >= DARK_SCENE_SKIN_GATE
        ):
            rescue_applied = True
            boosted_image = ImageEnhance.Contrast(
                ImageEnhance.Brightness(image).enhance(BRIGHTNESS_RESCUE_GAIN)
            ).enhance(CONTRAST_RESCUE_GAIN)
            rescue_sd_confidence, rescue_sd_has_nsfw, rescue_falcon_confidence = (
                self._classify_decision_detectors_from_image(boosted_image)
            )
            rescue_result = self._combine_nudity_signals(
                threshold=threshold,
                sd_confidence=max(sd_confidence, rescue_sd_confidence),
                sd_has_nsfw=bool(sd_has_nsfw or rescue_sd_has_nsfw),
                falcon_confidence=max(falcon_confidence, rescue_falcon_confidence),
                skin_confidence=skin_confidence,
                skin_has_nsfw=skin_has_nsfw,
                skin_ratio=skin_ratio,
                max_contour_ratio=max_contour_ratio,
            )
            rescue_triggered_by = [
                trigger for trigger in rescue_result["triggered_by"]
                if trigger not in classification["triggered_by"]
            ]
            rescue_result["mean_luma"] = classification["mean_luma"]
            classification = rescue_result

        classification["brightened_rescue_applied"] = rescue_applied
        classification["brightened_rescue_triggered_by"] = rescue_triggered_by
        return classification

    def _classify_decision_detectors_from_image(self, image: Image.Image) -> tuple[float, bool, float]:
        """Run the decision detectors (Stable Diffusion + Falcon) on a PIL image."""
        image_array = np.array(image)

        import torch

        safety_input = self.feature_extractor(images=image, return_tensors="pt").to(self._device)

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

        falcon_confidence = 0.0
        try:
            falcon_inputs = self.nsfw_processor(images=image, return_tensors="pt").to(self._device)
            with torch.no_grad():
                falcon_outputs = self.nsfw_model(**falcon_inputs)
                falcon_probs = torch.softmax(falcon_outputs.logits, dim=-1)
            falcon_confidence = float(falcon_probs[0][1].item())
        except Exception as e:
            logger.warning(f"Falconsai checker failed: {e}")

        return sd_confidence, bool(has_nsfw[0]), falcon_confidence

    @staticmethod
    def _mean_luma(image: Image.Image) -> float:
        """Return average frame brightness on a 0-255 grayscale scale."""
        grayscale = ImageOps.grayscale(image)
        return float(np.asarray(grayscale, dtype=np.float32).mean())

    def _combine_nudity_signals(
        self,
        *,
        threshold: float,
        sd_confidence: float,
        sd_has_nsfw: bool,
        falcon_confidence: float,
        skin_confidence: float,
        skin_has_nsfw: bool,
        skin_ratio: float,
        max_contour_ratio: float,
    ) -> dict:
        """Combine raw detector outputs into a single thresholded decision."""
        triggered_by = []
        if sd_has_nsfw:
            triggered_by.append("stable_diffusion")
        if falcon_confidence >= FALCON_TRIGGER_GATE:
            triggered_by.append("falcon")

        score = round(max(sd_confidence, falcon_confidence, skin_confidence), 4)
        threshold = round(float(threshold), 4)
        threshold_passed = bool(triggered_by) and score >= threshold
        return {
            "score": score,
            "sd_confidence": round(float(sd_confidence), 4),
            "falcon_confidence": round(float(falcon_confidence), 4),
            "skin_confidence": round(float(skin_confidence), 4),
            "skin_ratio": round(float(skin_ratio), 4),
            "max_contour_ratio": round(float(max_contour_ratio), 4),
            "triggered_by": triggered_by,
            "threshold": threshold,
            "threshold_passed": threshold_passed,
            "sd_has_nsfw": bool(sd_has_nsfw),
            "skin_has_nsfw": bool(skin_has_nsfw),
        }

    def _log_nudity_detection(
        self,
        *,
        timestamp: float,
        classification: dict,
        media_type: str,
        is_cartoon: bool,
        phase: str = "broad",
    ) -> None:
        """Emit concise INFO logs for interesting frames and full DEBUG details."""
        triggered_by = ",".join(classification.get("triggered_by", [])) or "none"
        message = (
            "Frame %.2fs [%s] %s media=%s cartoon=%s threshold=%.2f "
            "scores(sd=%.3f falcon=%.3f skin=%.3f ratio=%.3f contour=%.3f) "
            "triggers=%s pass=%s"
        )
        args = (
            timestamp,
            phase,
            "PASS" if classification["threshold_passed"] else "fail",
            media_type,
            is_cartoon,
            classification["threshold"],
            classification["sd_confidence"],
            classification["falcon_confidence"],
            classification["skin_confidence"],
            classification["skin_ratio"],
            classification["max_contour_ratio"],
            triggered_by,
            classification["threshold_passed"],
        )
        logger.debug(message, *args)
        if phase != "refine" and (
            classification["threshold_passed"]
            or classification["score"] >= self.candidate_threshold
            or classification["triggered_by"]
            or classification["sd_confidence"] >= classification["threshold"]
            or classification["falcon_confidence"] >= FALCON_TRIGGER_GATE
            or classification["skin_has_nsfw"]
        ):
            logger.info(message, *args)

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
        for seg in segments:
            seg.setdefault("topics", [])

        try:
            from analyzer.topic_classifier import LLMTopicClassifier
            clf = LLMTopicClassifier()

            # Build transcript segments for classification. With 5s BVF buckets,
            # most segments are silent/empty; avoid spending one LLM request per
            # empty bucket because that makes production runs crawl.
            transcript_segs = []
            segment_indices = []
            for idx, seg in enumerate(segments):
                start = seg.get("start_time", 0)
                end = seg.get("end_time", 0)
                transcript = self._extract_transcript_for_segment(start, end).strip()
                if not transcript:
                    continue
                segment_indices.append(idx)
                transcript_segs.append({
                    "transcript": transcript,
                    "start_time": start,
                    "end_time": end,
                })

            if not transcript_segs:
                return segments

            if len(transcript_segs) > 40:
                logger.info(
                    "Skipping LLM topic classification for %d short segments; "
                    "5s bucketed analysis would otherwise issue one request per segment.",
                    len(transcript_segs),
                )
                return segments

            classified = clf.classify_segments(transcript_segs)

            # Add topics back only to the non-empty transcript segments.
            for idx, cls in zip(segment_indices, classified):
                segments[idx]["topics"] = cls.get("topics", [])

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
        num_buckets = max(1, int(np.ceil(duration / self.frame_interval)))
        bucket_tags = [set() for _ in range(num_buckets)]

        for det in detections:
            start_time, end_time = self._detection_time_range(det, duration)
            if end_time <= start_time:
                continue

            tags = self._detection_tags(det)
            if not tags:
                continue

            for bucket_idx in range(num_buckets):
                bucket_start = bucket_idx * self.frame_interval
                bucket_end = min(duration, bucket_start + self.frame_interval)
                if start_time < bucket_end and end_time > bucket_start:
                    bucket_tags[bucket_idx].update(tags)

        segments = []
        for bucket_idx in range(num_buckets):
            start_time = round(bucket_idx * self.frame_interval, 2)
            end_time = round(min(duration, (bucket_idx + 1) * self.frame_interval), 2)
            tags = sorted(bucket_tags[bucket_idx])
            risk = "mature" if tags else "safe"
            segments.append({
                "id": f"seg_{bucket_idx + 1:03d}",
                "start_time": start_time,
                "end_time": end_time,
                "tags": tags,
                "risk": risk,
                "action": "swap" if risk == "mature" else "play",
            })

        return [seg for seg in segments if seg["end_time"] > seg["start_time"]]

    def _detection_time_range(self, detection: dict, duration: float) -> tuple[float, float]:
        start_time = float(detection.get("bad_start", detection.get("time", 0.0)))
        end_time = float(detection.get("bad_end", start_time + self.frame_interval))
        start_time = max(0.0, min(start_time, duration))
        end_time = max(start_time, min(end_time, duration))
        return start_time, end_time

    @staticmethod
    def _detection_tags(detection: dict) -> list[str]:
        if detection.get("tags"):
            return list(detection["tags"])
        detection_type = str(detection.get("type", "")).strip().lower()
        return [detection_type] if detection_type else []

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
                "action": seg.get("action", "play"),
            }
            if seg.get("profiles"):
                manifest_seg["profiles"] = seg["profiles"]
            if seg.get("profile_segment_id"):
                manifest_seg["profile_segment_id"] = seg["profile_segment_id"]
            if seg.get("is_filler"):
                manifest_seg["is_filler"] = True
            if seg.get("source_path"):
                manifest_seg["source_path"] = seg["source_path"]
                manifest_seg["source_start_time"] = seg.get("source_start_time", start_time)
                manifest_seg["source_end_time"] = seg.get("source_end_time", end_time)
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
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "profiles": DEFAULT_PROFILES,
            "segments": manifest_segments,
        }

    def _attach_goldylocks_fillers(self, segments: list[dict]) -> list[dict]:
        nudity_segments = [
            seg for seg in segments
            if not seg.get("is_filler") and "nudity" in set(seg.get("tags", []))
        ]
        if not nudity_segments:
            return segments

        if not self.goldylocks_filler_video.exists():
            logger.warning(
                "Goldilocks filler video is missing at %s; leaving nudity swap targets unchanged.",
                self.goldylocks_filler_video,
            )
            return segments

        try:
            filler_duration = self._get_duration_for_path(self.goldylocks_filler_video)
        except Exception as exc:
            logger.warning(
                "Failed to probe Goldilocks filler video at %s; leaving nudity swap targets unchanged: %s",
                self.goldylocks_filler_video,
                exc,
            )
            return segments

        updated_segments = [dict(seg) for seg in segments]
        filler_segments: list[dict] = []

        for seg in updated_segments:
            if seg.get("is_filler") or "nudity" not in set(seg.get("tags", [])):
                continue

            segment_duration = max(0.01, float(seg["end_time"]) - float(seg["start_time"]))
            try:
                selection = pick_filler_window(
                    duration=filler_duration,
                    desired_length=segment_duration,
                    seed=self._segment_seed(seg),
                )
            except ValueError as exc:
                logger.warning(
                    "Failed to select Goldilocks filler for %s; leaving original segment in place: %s",
                    seg["id"],
                    exc,
                )
                continue

            filler_id = f"filler_{len(filler_segments) + 1:03d}"
            seg["profile_segment_id"] = filler_id
            filler_segments.append({
                "id": filler_id,
                "start_time": 0.0,
                "end_time": round(selection.length, 3),
                "tags": [],
                "risk": "safe",
                "action": "play",
                "is_filler": True,
                "source_path": str(self.goldylocks_filler_video),
                "source_start_time": round(selection.start, 3),
                "source_end_time": round(selection.end, 3),
            })

        if filler_segments:
            logger.info(
                "Attached %d Goldilocks filler segment(s) for nudity swaps.",
                len(filler_segments),
            )
        return updated_segments + filler_segments

    def _segment_seed(self, segment: dict) -> int:
        seed_input = (
            f"{self.video_path}|{segment.get('id')}|"
            f"{segment.get('start_time', 0.0):.3f}|{segment.get('end_time', 0.0):.3f}"
        )
        return int(hashlib.sha256(seed_input.encode("utf-8")).hexdigest()[:8], 16)


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
        mature_start = round(one_third, 2)
        mature_end = round(one_third * 2, 2)
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0,
                "end_time": mature_start,
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
            {
                "id": "seg_002",
                "start_time": mature_start,
                "end_time": mature_end,
                "tags": ["gore"],
                "risk": "mature",
                "action": "skip",
            },
            {
                "id": "seg_003",
                "start_time": mature_end,
                "end_time": round(duration, 2),
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
        ]
        if self.demo_filler_video:
            if not self.demo_filler_video.exists():
                raise FileNotFoundError(f"Demo filler video not found: {self.demo_filler_video}")

            mature_duration = max(0.01, mature_end - mature_start)
            filler_duration = self._resolve_demo_filler_duration(mature_duration)
            segments[1]["profiles"] = {
                "child": {"action": "swap", "segment_id": "filler_001"},
                "teen_m": {"action": "swap", "segment_id": "filler_001"},
                "teen_f": {"action": "swap", "segment_id": "filler_001"},
                "adult": {"action": "play", "segment_id": "seg_002"},
            }
            segments.append({
                "id": "filler_001",
                "start_time": 0.0,
                "end_time": round(filler_duration, 2),
                "tags": [],
                "risk": "safe",
                "action": "play",
                "is_filler": True,
                "source_path": str(self.demo_filler_video),
                "source_start_time": round(self.demo_filler_start, 2),
                "source_end_time": round(self.demo_filler_start + filler_duration, 2),
            })

        segments = [s for s in segments if s["end_time"] > s["start_time"]]
        narrative_index = 1
        for seg in segments:
            if seg["id"].startswith("filler_"):
                continue
            seg["id"] = f"seg_{narrative_index:03d}"
            narrative_index += 1

        segments = self._snap_segments_to_keyframes(segments, self.video_path, duration)
        manifest = self._build_manifest(segments, duration)
        self._attach_media_assets(manifest["segments"])
        output_bvf = self._save_bvf(manifest)
        self.last_bvf_path = output_bvf
        logger.info(f"BVF saved to: {output_bvf}")
        return manifest

    def _get_keyframe_times(self, video_path: Path) -> list[float]:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-skip_frame", "nokey",
                "-show_frames",
                "-show_entries", "frame=best_effort_timestamp_time",
                "-of", "json",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        keyframes = []
        for frame in payload.get("frames", []):
            timestamp = frame.get("best_effort_timestamp_time")
            if timestamp is None:
                continue
            keyframes.append(float(timestamp))
        if not keyframes or keyframes[0] > 0.001:
            keyframes.insert(0, 0.0)
        return sorted(set(round(ts, 3) for ts in keyframes))

    @staticmethod
    def _nearest_keyframe(target: float, keyframes: list[float]) -> float:
        if not keyframes:
            return round(target, 3)
        idx = bisect_left(keyframes, target)
        if idx <= 0:
            return keyframes[0]
        if idx >= len(keyframes):
            return keyframes[-1]
        before = keyframes[idx - 1]
        after = keyframes[idx]
        if abs(after - target) < abs(target - before):
            return after
        return before

    def _snap_segments_to_keyframes(
        self,
        segments: list[dict],
        source_path: Path,
        duration: float,
    ) -> list[dict]:
        keyframes = self._get_keyframe_times(source_path)
        if not keyframes:
            return segments

        boundaries = [0.0]
        for seg in segments:
            if seg.get("is_filler"):
                continue
            boundaries.append(float(seg["end_time"]))

        snapped = [0.0]
        for boundary in boundaries[1:-1]:
            snapped.append(self._nearest_keyframe(boundary, keyframes))
        snapped.append(round(duration, 3))

        normalized = [snapped[0]]
        for boundary in snapped[1:]:
            boundary = round(boundary, 3)
            if boundary < normalized[-1]:
                boundary = normalized[-1]
            normalized.append(boundary)

        snapped_segments: list[dict] = []
        narrative_index = 0
        for seg in segments:
            updated = dict(seg)
            if updated.get("is_filler"):
                snapped_segments.append(updated)
                continue
            updated["start_time"] = round(normalized[narrative_index], 3)
            updated["end_time"] = round(normalized[narrative_index + 1], 3)
            narrative_index += 1
            snapped_segments.append(updated)
        return [seg for seg in snapped_segments if seg["end_time"] > seg["start_time"]]

    def _resolve_demo_filler_duration(self, default_duration: float) -> float:
        filler_duration = self.demo_filler_duration or default_duration
        filler_total_duration = self._get_duration_for_path(self.demo_filler_video)
        filler_available = max(0.01, filler_total_duration - self.demo_filler_start)
        return round(min(filler_duration, filler_available), 2)

    def _get_duration_for_path(self, video_path: Path | None) -> float:
        if video_path is None:
            raise ValueError("video_path is required")
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())

    def _attach_media_assets(self, segments: list[dict]) -> None:
        """Embed self-contained fMP4/CMAF media assets for every segment."""
        with tempfile.TemporaryDirectory(prefix="bvf_analyzer_segments_") as tmp:
            tmp_dir = Path(tmp)
            for seg in segments:
                segment_path = tmp_dir / f"{seg['id']}.mp4"
                source_path = Path(seg.get("source_path", self.video_path))
                source_start = seg.get("source_start_time", seg["start_time"])
                source_end = seg.get("source_end_time", seg["end_time"])
                self._remux_segment(source_path, source_start, source_end, segment_path)
                seg["media_container"] = "fmp4"
                seg["media_payload"] = segment_path.read_bytes()

    def _remux_segment(
        self,
        source_path: Path,
        start_time: float,
        end_time: float,
        output_path: Path,
    ) -> None:
        """Extract a segment as a self-contained fragmented MP4 asset."""
        duration = max(0.001, end_time - start_time)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss", f"{start_time:.3f}",
                "-i", str(source_path),
                "-t", f"{duration:.3f}",
                "-map", "0:v:0",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-avoid_negative_ts", "make_zero",
                "-reset_timestamps", "1",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4",
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
        default=0.75,
        help="NSFW confidence threshold (default: 0.75)",
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
        "--scan-interval",
        type=float,
        default=None,
        help="Broad-pass scan interval in seconds (default: use --interval)",
    )
    parser.add_argument(
        "--candidate-threshold",
        type=float,
        default=0.25,
        help="Broad-pass candidate score threshold (default: 0.25)",
    )
    parser.add_argument(
        "--dense-rescan-fps",
        type=float,
        default=2.0,
        help="Dense-pass rescan FPS inside candidate windows (default: 2.0)",
    )
    parser.add_argument(
        "--dense-window-padding",
        type=float,
        default=5.0,
        help="Padding around broad-pass candidate timestamps in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--min-positive-frames",
        type=int,
        default=2,
        help="Dense-pass final-threshold frames required to confirm mature content (default: 2)",
    )
    parser.add_argument(
        "--debug-contact-sheet",
        default=None,
        help="Optional output path for a broad/dense debug contact sheet",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: same as video file)",
    )
    parser.add_argument(
        "--skin-debug-timestamps",
        type=float,
        nargs="+",
        default=None,
        help="Export HSV skin-mask diagnostics for the given timestamps and exit",
    )
    parser.add_argument(
        "--skin-debug-dir",
        default=None,
        help="Output directory for --skin-debug-timestamps PNGs",
    )
    parser.add_argument(
        "--demo-branch",
        action="store_true",
        help="Create a deterministic safe/mature/safe BVF without loading ML models",
    )
    parser.add_argument(
        "--demo-filler-video",
        default=None,
        help="Demo-branch only: replacement clip to embed as filler media for mature segments",
    )
    parser.add_argument(
        "--demo-filler-start",
        type=float,
        default=0.0,
        help="Demo-branch only: start offset in the filler video (seconds)",
    )
    parser.add_argument(
        "--demo-filler-duration",
        type=float,
        default=None,
        help="Demo-branch only: replacement clip duration in seconds (defaults to mature segment length)",
    )
    args = parser.parse_args()

    analyzer = MovieAnalyzer(
        video_path=args.video,
        output_dir=args.output_dir,
        whisper_model=args.model,
        nsfw_threshold=args.threshold,
        cartoon_threshold=args.cartoon_threshold,
        frame_interval=args.interval,
        scan_interval=args.scan_interval,
        candidate_threshold=args.candidate_threshold,
        dense_rescan_fps=args.dense_rescan_fps,
        dense_window_padding=args.dense_window_padding,
        min_positive_frames=args.min_positive_frames,
        debug_contact_sheet=args.debug_contact_sheet,
        load_models=not args.demo_branch and not args.skin_debug_timestamps,
        demo_filler_video=args.demo_filler_video,
        demo_filler_start=args.demo_filler_start,
        demo_filler_duration=args.demo_filler_duration,
    )

    try:
        if args.skin_debug_timestamps:
            outputs = analyzer.export_skin_diagnostics(
                timestamps=args.skin_debug_timestamps,
                output_dir=args.skin_debug_dir,
            )
            print("\nSkin diagnostics complete.")
            for path in outputs:
                print(f"   {path}")
            return
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
