"""
Skin Detector (HSV-based)
Simple but effective skin tone detection using HSV color space analysis.
Works better than NSFW classifiers on older films with different lighting/aesthetics.
"""
import logging
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np
from PIL import Image

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
        try:
            # Load image
            image = cv2.imread(str(frame_path))
            if image is None:
                return 0.0, False

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
            skin_ratio = skin_pixels / total_pixels if total_pixels > 0 else 0

            # Check for large contiguous skin regions (nudity has larger areas)
            # Find contours in the skin mask
            contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            max_contour_area = max([cv2.contourArea(c) for c in contours]) if contours else 0
            max_contour_ratio = max_contour_area / total_pixels

            # Heuristic thresholds:
            # skin_ratio > 0.40: Very high skin coverage
            # max_contour_ratio > 0.15: Large contiguous skin region
            # Both conditions needed for NSFW flag
            confidence = min(1.0, (skin_ratio + max_contour_ratio) / 0.6)
            has_nsfw = skin_ratio > 0.40 and max_contour_ratio > 0.15

            return confidence, has_nsfw

        except Exception as e:
            logger.warning(f"Skin detection failed: {e}")
            return 0.0, False
