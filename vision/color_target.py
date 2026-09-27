"""
Deljena logika za pracenje objekta po boji (HSV threshold) - izdvojeno iz
hand_tracker.py da bi i point_at_object.py mogao da je koristi bez
dupliranja koda.
"""
from __future__ import annotations

import cv2
import numpy as np

MIN_TARGET_AREA = 200  # px^2 - manje konture od ovoga se ignorisu (sum)

DEFAULT_HSV_LOWER = (35, 80, 80)   # zelena, podesi za svoj objekat preko --tune
DEFAULT_HSV_UPPER = (85, 255, 255)


def parse_hsv(text: str) -> tuple[int, int, int]:
    parts = tuple(int(x) for x in text.split(","))
    if len(parts) != 3:
        raise ValueError(f"HSV mora imati 3 broja odvojena zarezom, dobio sam: {text}")
    return parts  # type: ignore[return-value]


def find_target(frame_bgr, hsv_lower, hsv_upper):
    """Vraca ((x, y, radius), mask) najveceg bloba u zadatom HSV opsegu, ili (None, mask)."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lower), np.array(hsv_upper))
    mask = cv2.erode(mask, None, iterations=2)
    mask = cv2.dilate(mask, None, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, mask
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < MIN_TARGET_AREA:
        return None, mask
    (x, y), radius = cv2.minEnclosingCircle(c)
    return (x, y, radius), mask
