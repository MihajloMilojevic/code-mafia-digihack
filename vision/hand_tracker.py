"""
Samostalni hand-tracking prototip - NEZAVISAN od robota i od arm_control_app-a.

Prati DVE stvari u frejmu:
  1. saku (MediaPipe HandLandmarker, Tasks API)
  2. CILJNI OBJEKAT (praceno po boji preko color_target.py - HSV threshold,
     podesi opseg za svoj predmet preko --tune ili --target-lower/--target-upper)

I STAMPA U KONZOLU komandu smera koju treba da se saka pomeri DA BI
STIGLA DO OBJEKTA.

NE zove /api/nudge niti bilo sta drugo - to je namerno (to radi
point_at_object.py, koji ne prati ljudsku saku nego direktno usmerava
ruku ROBOTA ka objektu).

Model za saku se automatski preuzima u vision/models/hand_landmarker.task
na prvo pokretanje (par MB, treba internet samo tada).

PRIKAZ UZIVO - dve opcije, headless SSH na PC2 nema ni jednu po defaultu:
  --http-port 8092   servira anotiran frejm kao MJPEG-stil HTTP stranicu
                      (isti obrazac kao ros_camera_viewer.py) - radi svuda,
                      ukljucujuci headless SSH, gleda se u browseru.
  (bez --no-preview)  cv2.imshow prozor - zahteva DISPLAY (npr. 'ssh -X').
                      Automatski se gasi (bez pada) ako DISPLAY ne postoji.
Oba mogu da rade istovremeno.

Podesavanje boje cilja:
  python hand_tracker.py --tune
    - otvara prozor sa 6 klizaca (H/S/V min/max) i live prikazom maske
    - MORA imati DISPLAY (vidi napomenu gore) - ako radis headless na PC2,
      pokreni --tune na svom laptopu (vebkamera/telefon), pa prenesi
      dobijene --target-lower/--target-upper brojeve na PC2.

Izvor kamere (--source ili HAND_TRACKER_SOURCE env):
  --source 0                               lokalna vebkamera (indeks 0)
  --source http://192.168.1.50:8080/video  telefon sa "IP Webcam" (Android)
  --source ros2:left                       prava A2 CHEST_LEFT_FISHEYE (PC2)
  --source ros2:right                      prava A2 CHEST_RIGHT_FISHEYE (PC2)

NAPOMENA o pravim A2 fisheye kamerama (CHEST_LEFT/RIGHT_FISHEYE):
- POTVRDJENO (2026-09-26, iz vendorovanog ros2_capture.py): idu preko
  ROS2 (ne V4L/cv2.VideoCapture direktno) - vidi frame_source.py.
  ros2:left/right MORA se pokrenuti tamo gde je ROS2/rclpy vec instaliran
  (isto okruzenje kao postojeci robot_services/vision/detection na PC2).
- Namerno NE mirroujem sliku - "levo/desno" je iz UGLA KAMERE, ne iz ugla
  posmatraca ispred kamere.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    RunningMode,
)

from color_target import DEFAULT_HSV_LOWER, DEFAULT_HSV_UPPER, find_target, parse_hsv
from http_preview import publish_frame, start_http_preview

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

DEADZONE_FRAC = 0.08     # % poluprecnika frejma - unutar ovoga = "na cilju"
PRINT_INTERVAL_S = 0.3
WRIST_LANDMARK_IDX = 0

def ensure_model() -> str:
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        print(f"[hand_tracker] preuzimam model u {MODEL_PATH} ...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("[hand_tracker] model preuzet.")
    return MODEL_PATH


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


def _preview_possible() -> bool:
    """Da li je bezbedno pozvati cv2.imshow/namedWindow.

    Bez ovoga, Qt na headless Linux SSH sesiji (bez X11 forwarding-a) ne
    baca Python izuzetak - zove abort() na C nivou ('Aborted (core dumped)'),
    sto se NE MOZE uhvatiti sa try/except. Zato se ovo mora proveriti PRE
    prvog GUI poziva, ne posle."""
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY"))
    return True  # macOS/Windows - pretpostavi da postoji displej


def run_tune(source: str, hsv_lower: list[int], hsv_upper: list[int]) -> None:
    """Interaktivno podesavanje HSV opsega preko trackbar-ova. Zahteva DISPLAY
    (nema headless varijantu - svrha mu je zivo gledanje maske)."""
    if not _preview_possible():
        print("[hand_tracker] Nema DISPLAY okruzenja (verovatno SSH bez X11 "
              "forwarding-a) - --tune MORA da ima ziv prozor. Pokreni --tune "
              "na masini sa displejem (npr. svoj laptop, sa vebkamerom ili "
              "telefonom), zapamti --target-lower/--target-upper brojeve, pa "
              "ih prosledi kad pokreces glavnu petlju na PC2. Alternativa: "
              "'ssh -X'/'ssh -Y' do PC2 ako imas X server lokalno.")
        return

    import numpy as np

    from frame_source import open_source
    cap = open_source(source)

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


def run(source: str, show_preview: bool, http_port: int | None, hsv_lower, hsv_upper) -> None:
    if show_preview and not _preview_possible():
        print("[hand_tracker] Nema DISPLAY okruzenja - iskljucujem cv2.imshow "
              "prozor da izbegnem Qt/X11 pad (Aborted/core dumped). Konzolni "
              "ispis i dalje radi. Koristi --http-port za prikaz preko "
              "browsera na headless masini.")
        show_preview = False

    if http_port is not None:
        start_http_preview(http_port)

    model_path = ensure_model()

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=RunningMode.IMAGE,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )

    from frame_source import open_source
    cap = open_source(source)

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

            if show_preview or http_port is not None:
                if hand_point is not None:
                    cv2.circle(frame, (int(hand_point[0]), int(hand_point[1])), 8, (0, 255, 0), -1)
                if target is not None:
                    tx, ty, radius = target
                    cv2.circle(frame, (int(tx), int(ty)), int(radius), (255, 200, 0), 2)
                if hand_point is not None and target is not None:
                    cv2.line(frame, (int(hand_point[0]), int(hand_point[1])),
                              (int(target[0]), int(target[1])), (0, 165, 255), 2)

            if http_port is not None:
                publish_frame(frame)

            if show_preview:
                cv2.imshow("hand_tracker (q za izlaz)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    cap.release()
    if show_preview:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("HAND_TRACKER_SOURCE", "0"))
    parser.add_argument("--no-preview", action="store_true",
                         help="iskljuci cv2.imshow (bezuslovno - koristi ovo na headless masini)")
    parser.add_argument("--http-port", type=int, default=None,
                         help="servira anotiran frejm preko HTTP-a na ovom portu (radi headless)")
    parser.add_argument("--tune", action="store_true",
                         help="otvori interaktivni HSV tuner umesto glavne petlje (zahteva DISPLAY)")
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
        run(args.source, show_preview=not args.no_preview, http_port=args.http_port,
            hsv_lower=hsv_lower, hsv_upper=hsv_upper)
