"""
Samostalni hand-tracking prototip - NEZAVISAN od robota i od arm_control_app-a.

Prati DVE stvari u frejmu:
  1. saku (MediaPipe HandLandmarker, Tasks API)
  2. CILJNI OBJEKAT (praceno po boji, HSV threshold - podesi opseg za
     svoj predmet preko --tune ili --target-lower/--target-upper)

I STAMPA U KONZOLU komandu smera koju treba da se saka pomeri DA BI
STIGLA DO OBJEKTA - ne vise do centra ekrana.

NE zove /api/nudge niti bilo sta drugo - to je namerno. Sledeci korak,
kad ovo bude pouzdano, je zameniti print() jednim HTTP pozivom.

Model za saku se automatski preuzima u vision/models/hand_landmarker.task
na prvo pokretanje (par MB, treba internet samo tada).

Podesavanje boje cilja:
  python hand_tracker.py --tune
    - otvara prozor sa 6 klizaca (H/S/V min/max) i live prikazom maske
    - pomeri klizace dok cilj (i SAMO cilj) ne postane beo u masci
    - ctrl+c u terminalu ispisuje trenutne vrednosti da ih zapamtis

  python hand_tracker.py --target-lower 35,80,80 --target-upper 85,255,255
    - koristi te vrednosti umesto default (zelena) opsega

Izvor kamere (--source ili HAND_TRACKER_SOURCE env):
  --source 0                               lokalna vebkamera (indeks 0)
  --source http://192.168.1.50:8080/video  telefon sa "IP Webcam" (Android)

NAPOMENA o pravim A2 fisheye kamerama (CHEST_LEFT/RIGHT_FISHEYE):
- Nemamo kalibracione parametre - drugaciji kod (ROS2), van obima ovog fajla.
- Namerno NE mirroujem sliku - "levo/desno" je iz UGLA KAMERE, ne iz ugla
  posmatraca ispred kamere. Proveri ovo prvo ako se smerovi cine obrnuti.
"""
from __future__ import annotations

import argparse
import math
import os
import time
import urllib.request

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    RunningMode,
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

DEADZONE_FRAC = 0.08     # % poluprecnika frejma - unutar ovoga = "na cilju"
PRINT_INTERVAL_S = 0.3
WRIST_LANDMARK_IDX = 0
MIN_TARGET_AREA = 200    # px^2 - manje konture od ovoga se ignorisu (sum)

DEFAULT_HSV_LOWER = (35, 80, 80)   # zelena, podesi za svoj objekat
DEFAULT_HSV_UPPER = (85, 255, 255)


def ensure_model() -> str:
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        print(f"[hand_tracker] preuzimam model u {MODEL_PATH} ...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("[hand_tracker] model preuzet.")
    return MODEL_PATH


def parse_hsv(text: str) -> tuple[int, int, int]:
    parts = tuple(int(x) for x in text.split(","))
    if len(parts) != 3:
        raise ValueError(f"HSV mora imati 3 broja odvojena zarezom, dobio sam: {text}")
    return parts  # type: ignore[return-value]


def find_target(frame_bgr, hsv_lower, hsv_upper):
    """Vraca (x, y, radius) najveceg bloba u zadatom HSV opsegu, ili None."""
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


def classify_direction_to_target(hx: float, hy: float, tx: float, ty: float, w: int, h: int) -> tuple[str, float]:
    """Vektor OD SAKE KA OBJEKTU. Vraca (komanda, jacina 0..1)."""
    dx = (tx - hx) / (w / 2)
    dy = (ty - hy) / (h / 2)
    dist_frac = math.hypot(dx, dy)

    if dist_frac < DEADZONE_FRAC:
        return "na cilju", 0.0

    if abs(dx) > abs(dy):
        return ("desno" if dx > 0 else "levo"), min(abs(dx), 1.0)
    return ("dole" if dy > 0 else "gore"), min(abs(dy), 1.0)


def run_tune(source: str, hsv_lower: list[int], hsv_upper: list[int]) -> None:
    """Interaktivno podesavanje HSV opsega preko trackbar-ova."""
    cap_source = int(source) if source.isdigit() else source
    cap = cv2.VideoCapture(cap_source)
    if not cap.isOpened():
        raise RuntimeError(f"Ne mogu da otvorim izvor kamere: {source}")

    win = "podesi HSV (q za izlaz, ispisuje vrednosti)"
    cv2.namedWindow(win)
    labels = ["H min", "H max", "S min", "S max", "V min", "V max"]
    values = [hsv_lower[0], hsv_upper[0], hsv_lower[1], hsv_upper[1], hsv_lower[2], hsv_upper[2]]
    maxes = [179, 179, 255, 255, 255, 255]
    for label, val, mx in zip(labels, values, maxes):
        cv2.createTrackbar(label, win, val, mx, lambda _: None)

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            vals = [cv2.getTrackbarPos(l, win) for l in labels]
            lower = np.array([vals[0], vals[2], vals[4]])
            upper = np.array([vals[1], vals[3], vals[5]])
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, lower, upper)
            combined = np.hstack([frame, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)])
            cv2.imshow(win, combined)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print(f"[hand_tracker] --target-lower {vals[0]},{vals[2]},{vals[4]} "
                      f"--target-upper {vals[1]},{vals[3]},{vals[5]}")
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


def run(source: str, show_preview: bool, hsv_lower, hsv_upper) -> None:
    model_path = ensure_model()

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=RunningMode.IMAGE,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )

    cap_source = int(source) if source.isdigit() else source
    cap = cv2.VideoCapture(cap_source)
    if not cap.isOpened():
        raise RuntimeError(f"Ne mogu da otvorim izvor kamere: {source}")

    last_print = 0.0
    with HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[hand_tracker] frejm nije procitan, pokusavam dalje...")
                time.sleep(0.2)
                continue

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            hand_result = landmarker.detect(mp_image)
            target, _mask = find_target(frame, hsv_lower, hsv_upper)

            hand_point = None
            if hand_result.hand_landmarks:
                lm = hand_result.hand_landmarks[0][WRIST_LANDMARK_IDX]
                hand_point = (lm.x * w, lm.y * h)

            now = time.time()
            if now - last_print > PRINT_INTERVAL_S:
                if hand_point is None:
                    print("[hand_tracker] saka nije pronadjena")
                elif target is None:
                    print("[hand_tracker] cilj (objekat) nije pronadjen - proveri HSV opseg (--tune)")
                else:
                    hx, hy = hand_point
                    tx, ty, _radius = target
                    direction, strength = classify_direction_to_target(hx, hy, tx, ty, w, h)
                    print(f"[hand_tracker] saka=({hx:.0f},{hy:.0f}) cilj=({tx:.0f},{ty:.0f})  "
                          f"komanda={direction}  jacina={strength:.2f}")
                last_print = now

            if show_preview:
                if hand_point is not None:
                    cv2.circle(frame, (int(hand_point[0]), int(hand_point[1])), 8, (0, 255, 0), -1)
                if target is not None:
                    tx, ty, radius = target
                    cv2.circle(frame, (int(tx), int(ty)), int(radius), (255, 200, 0), 2)
                if hand_point is not None and target is not None:
                    cv2.line(frame, (int(hand_point[0]), int(hand_point[1])),
                              (int(target[0]), int(target[1])), (0, 165, 255), 2)
                cv2.imshow("hand_tracker (q za izlaz)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    cap.release()
    if show_preview:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("HAND_TRACKER_SOURCE", "0"))
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--tune", action="store_true",
                         help="otvori interaktivni HSV tuner umesto glavne petlje")
    parser.add_argument("--target-lower", default=",".join(map(str, DEFAULT_HSV_LOWER)),
                         help="H,S,V donja granica boje cilja, npr. 35,80,80")
    parser.add_argument("--target-upper", default=",".join(map(str, DEFAULT_HSV_UPPER)),
                         help="H,S,V gornja granica boje cilja, npr. 85,255,255")
    args = parser.parse_args()

    hsv_lower = parse_hsv(args.target_lower)
    hsv_upper = parse_hsv(args.target_upper)

    if args.tune:
        run_tune(args.source, list(hsv_lower), list(hsv_upper))
    else:
        run(args.source, show_preview=not args.no_preview, hsv_lower=hsv_lower, hsv_upper=hsv_upper)