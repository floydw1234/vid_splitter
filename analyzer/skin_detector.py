"""
Skin Detector (HSV-based)
Simple but effective skin tone detection using HSV color space analysis.
Works better than NSFW classifiers on older films with different lighting/aesthetics.
"""
# heuristic and conservative: this favors missing edge cases over over-flagging.
import logging
from pathlib import Path
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)


class SkinDetector:
    """Detects potential nudity via skin tone analysis in HSV color space.

    Strategy:
    1. Convert frame to HSV color space
    2. Create skin tone mask using multiple HSV ranges
    3. Calculate skin-to-frame ratio
    4. If ratio exceeds threshold, flag as potential nudity

    This works better on older films where NSFW classifiers fail due to
    different lighting, color grading, and film grain.
    """

    NSFW_SKIN_RATIO_THRESHOLD = 0.60
    NSFW_CONTOUR_RATIO_THRESHOLD = 0.30
    CONFIDENCE_SKIN_RATIO_REFERENCE = 0.80
    CONFIDENCE_CONTOUR_RATIO_REFERENCE = 0.50

    def __init__(self):
        logger.info("Loading Skin Detector (HSV-based)...")
        logger.info("Skin Detector loaded.")

    def analyze_frame(self, frame_path: Path) -> Tuple[float, bool]:
        """Analyze a frame for potential nudity via skin detection.

        Args:
            frame_path: Path to the image file.

        Returns:
            Tuple of (confidence, has_nsfw). Confidence is 0.0-1.0.
        """
        details = self.analyze_frame_details(frame_path)
        return details["confidence"], details["has_nsfw"]

    def analyze_frame_details(self, frame_path: Path) -> dict:
        """Analyze a frame and return structured skin metrics for debugging."""
        try:
            import cv2

            # Load image
            image = cv2.imread(str(frame_path))
            if image is None:
                return self._empty_result()
            analysis = self._analyze_image(image)
            return self._build_result(
                analysis["skin_ratio"],
                analysis["max_contour_ratio"],
            )

        except Exception as e:
            logger.warning(f"Skin detection failed: {e}")
            return self._empty_result()

    def create_debug_visualization(self, frame_path: Path) -> dict:
        """Analyze a frame and return reusable visualization assets."""
        try:
            import cv2

            image = cv2.imread(str(frame_path))
            if image is None:
                return self._empty_visualization_result()

            analysis = self._analyze_image(image)
            result = self._build_result(
                analysis["skin_ratio"],
                analysis["max_contour_ratio"],
            )

            original_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            mask_rgb = cv2.cvtColor(analysis["mask"], cv2.COLOR_GRAY2RGB)

            highlighted_rgb = original_rgb.copy()
            highlighted_rgb[analysis["mask"] > 0] = np.array([255, 191, 0], dtype=np.uint8)
            highlighted_rgb = cv2.addWeighted(original_rgb, 0.65, highlighted_rgb, 0.35, 0)
            if analysis["contours"]:
                cv2.drawContours(highlighted_rgb, analysis["contours"], -1, (0, 255, 0), 2)

            return {
                **result,
                "original_rgb": original_rgb,
                "mask_rgb": mask_rgb,
                "highlighted_rgb": highlighted_rgb,
            }

        except Exception as e:
            logger.warning(f"Skin debug visualization failed: {e}")
            return self._empty_visualization_result()

    def _analyze_image(self, image: np.ndarray) -> dict:
        import cv2

        # Convert to HSV
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        # Define multiple skin tone ranges in HSV
        # Range 1: Standard skin tones
        lower_skin1 = np.array([0, 48, 40])
        upper_skin1 = np.array([20, 255, 255])

        # Range 2: Darker skin tones
        lower_skin2 = np.array([0, 30, 20])
        upper_skin2 = np.array([15, 200, 200])

        # Range 3: Lighter skin tones
        lower_skin3 = np.array([0, 20, 100])
        upper_skin3 = np.array([15, 150, 255])

        # Create masks
        mask1 = cv2.inRange(hsv, lower_skin1, upper_skin1)
        mask2 = cv2.inRange(hsv, lower_skin2, upper_skin2)
        mask3 = cv2.inRange(hsv, lower_skin3, upper_skin3)

        # Combine masks
        combined_mask = cv2.bitwise_or(mask1, mask2)
        combined_mask = cv2.bitwise_or(combined_mask, mask3)

        # Morphological operations to clean up noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)

        # Calculate skin ratio
        total_pixels = image.shape[0] * image.shape[1]
        skin_pixels = cv2.countNonZero(combined_mask)
        skin_ratio = skin_pixels / total_pixels if total_pixels > 0 else 0.0

        # Check for large contiguous skin regions (nudity has larger areas)
        # Find contours in the skin mask
        contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        max_contour_area = max([cv2.contourArea(c) for c in contours]) if contours else 0.0
        max_contour_ratio = max_contour_area / total_pixels if total_pixels > 0 else 0.0

        return {
            "mask": combined_mask,
            "contours": contours,
            "skin_ratio": skin_ratio,
            "max_contour_ratio": max_contour_ratio,
        }

    def _build_result(self, skin_ratio: float, max_contour_ratio: float) -> dict:
        confidence, has_nsfw = self._evaluate_skin_regions(skin_ratio, max_contour_ratio)
        return {
            "confidence": confidence,
            "has_nsfw": has_nsfw,
            "skin_ratio": round(float(skin_ratio), 4),
            "max_contour_ratio": round(float(max_contour_ratio), 4),
        }

    @classmethod
    def _evaluate_skin_regions(cls, skin_ratio: float, max_contour_ratio: float) -> Tuple[float, bool]:
        """Turn raw skin metrics into a conservative NSFW confidence."""
        normalized_skin = min(1.0, max(0.0, skin_ratio) / cls.CONFIDENCE_SKIN_RATIO_REFERENCE)
        normalized_contour = min(
            1.0,
            max(0.0, max_contour_ratio) / cls.CONFIDENCE_CONTOUR_RATIO_REFERENCE,
        )
        confidence = round((normalized_skin * 0.6) + (normalized_contour * 0.4), 4)
        has_nsfw = (
            skin_ratio >= cls.NSFW_SKIN_RATIO_THRESHOLD
            and max_contour_ratio >= cls.NSFW_CONTOUR_RATIO_THRESHOLD
        )
        return confidence, has_nsfw

    @staticmethod
    def _empty_result() -> dict:
        return {
            "confidence": 0.0,
            "has_nsfw": False,
            "skin_ratio": 0.0,
            "max_contour_ratio": 0.0,
        }

    @classmethod
    def _empty_visualization_result(cls) -> dict:
        return {
            **cls._empty_result(),
            "original_rgb": None,
            "mask_rgb": None,
            "highlighted_rgb": None,
        }
