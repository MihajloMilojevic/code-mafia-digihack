"""
Spaja hand_tracker.py (detekcija saka, vizuelno) i detekciju OPSTEG
objekta po KATEGORIJI (ne po boji - vidi object_detector.py) u JEDNU
petlju - prati oboje istovremeno, i usmerava ruku ROBOTA ka objektu.

VAZNA RAZLIKA u odnosu na dva odvojena skripta:
- SAKA (MediaPipe HandLandmarker) se detektuje i CRTA radi konteksta/
  demoa - ali NE koristi se za racunanje nudge komande. Robotova
  sopstvena ruka nije ljudska saka, MediaPipe je nece prepoznati.
- Nudge komanda se racuna iz OBJEKAT-a u odnosu na CENTAR SLIKE - jedina
  geometrijski smislena referenca koju imamo bez kalibracije kamera<->rame.
- Objekat se sad trazi preko object_detector.py (MediaPipe ObjectDetector,
  EfficientDet-Lite0, COCO kategorije) umesto HSV boje - zadaje se preko
  --object-label (npr. 'bottle', 'cup', 'cell phone'). Bez --object-label,
  detektuje NAJBOLJU detekciju BILO KOJE kategorije - korisno prvi put da
  vidis koje labele model uopste prepoznaje u tvojoj sceni (prati ispis).

PODRAZUMEVANO JE DRY-RUN - isti princip kao ostatak projekta. --execute
stvarno salje HTTP pozive ka /api/nudge.

NAPOMENE/NEPOZNANICE (iste kao ranije):
- SIGN_ABDUCT_MATCHES_SCREEN_RIGHT nije provereno na robotu.
"""
from __future__ import annotations

import argparse
import os
import time

import cv2
import mediapipe as mp
import requests
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import HandLandmarker, HandLandmarkerOptions, RunningMode

from http_preview import publish_frame, start_http_preview
from object_detector import create_detector, find_object

HAND_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
HAND_MODEL_PATH = os.path.join(HAND_MODEL_DIR, "hand_landmarker.task")
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
WRIST_LANDMARK_IDX = 0

ARM_CONTROL_URL = os.getenv("ARM_CONTROL_URL", "http://127.0.0.1:8000")
DEADZONE_FRAC = 0.10
NUDGE_AMOUNT_RAD = 0.08
STATUS_INTERVAL_S = 1.0

# TODO: PROVERI na robotu
ACTING_SIDE = "right"
SIGN_ABDUCT_MATCHES_SCREEN_RIGHT = True


def ensure_hand_model() -> str:
    if not os.path.exists(HAND_MODEL_PATH):
        os.makedirs(HAND_MODEL_DIR, exist_ok=True)
        print(f"[track_and_point] preuzimam model saka u {HAND_MODEL_PATH} ...")
        import urllib.request
        urllib.request.urlretrieve(HAND_MODEL_URL, HAND_MODEL_PATH)
        print("[track_and_point] model preuzet.")
    return HAND_MODEL_PATH


def send_nudge(direction: str, dry_run: bool) -> None:
    print(f"[track_and_point] nudge -> side={ACTING_SIDE} direction={direction}"
          + ("  [DRY-RUN, nista nije poslato]" if dry_run else ""))
    if dry_run:
        return
    try:
        resp = requests.post(
            f"{ARM_CONTROL_URL}/api/nudge",
            json={"side": ACTING_SIDE, "direction": direction, "amount_rad": NUDGE_AMOUNT_RAD},
            timeout=3,
        )
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if data.get("at_limit"):
            print(f"[track_and_point]   *** GRANICA DOSTIGNUTA *** {data.get('detail')}")
        else:
            print(f"[track_and_point]   -> HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[track_and_point]   nudge NIJE uspeo: {e}")


def run(source: str, object_label: str | None, score_threshold: float, model_variant: str,
        dry_run: bool, interval_s: float, http_port: int | None) -> None:
    if http_port is not None:
        start_http_preview(http_port)

    hand_model_path = ensure_hand_model()
    hand_options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=hand_model_path),
        running_mode=RunningMode.IMAGE,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )
    object_detector = create_detector(object_label, score_threshold=score_threshold, variant=model_variant)

    from frame_source import open_source
    cap = open_source(source)

    print(f"[track_and_point] {'DRY-RUN (samo ispis)' if dry_run else '*** LIVE - salje na robota ***'}"
          f"  side={ACTING_SIDE}  interval={interval_s}s  "
          f"object_label={object_label or '(bilo koja kategorija)'}")
    if not dry_run:
        print("[track_and_point] Ctrl+C za prekid u svakom trenutku.")

    last_nudge = 0.0
    last_status = 0.0
    with HandLandmarker.create_from_options(hand_options) as hand_landmarker:
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    print("[track_and_point] frejm nije procitan, pokusavam dalje...")
                    time.sleep(0.2)
                    continue

                h, w = frame.shape[:2]

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                hand_result = hand_landmarker.detect(mp_image)
                hand_point = None
                if hand_result.hand_landmarks:
                    lm = hand_result.hand_landmarks[0][WRIST_LANDMARK_IDX]
                    hand_point = (lm.x * w, lm.y * h)

                target, _all_detections = find_object(object_detector, frame)

                if http_port is not None:
                    annotated = frame.copy()
                    cv2.circle(annotated, (w // 2, h // 2), 6, (255, 255, 255), 1)
                    if hand_point is not None:
                        cv2.circle(annotated, (int(hand_point[0]), int(hand_point[1])), 8, (0, 255, 0), -1)
                    if target is not None:
                        tx_, ty_, r_, label_, score_ = target
                        cv2.circle(annotated, (int(tx_), int(ty_)), int(r_), (255, 200, 0), 2)
                        cv2.line(annotated, (w // 2, h // 2), (int(tx_), int(ty_)), (0, 165, 255), 2)
                        cv2.putText(annotated, f"{label_} {score_:.2f}", (int(tx_) - 20, int(ty_) - int(r_) - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 2)
                    if hand_point is not None and target is not None:
                        cv2.line(annotated, (int(hand_point[0]), int(hand_point[1])),
                                  (int(target[0]), int(target[1])), (200, 200, 200), 1)
                    if target is None:
                        cv2.putText(annotated, "objekat nije pronadjen", (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    if hand_point is None:
                        cv2.putText(annotated, "saka nije pronadjena", (10, 55),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    publish_frame(annotated)

                now = time.time()
                if now - last_status > STATUS_INTERVAL_S:
                    hand_txt = f"({hand_point[0]:.0f},{hand_point[1]:.0f})" if hand_point else "NIJE NADJENA"
                    if target:
                        obj_txt = f"({target[0]:.0f},{target[1]:.0f}) label={target[3]!r} score={target[4]:.2f}"
                    else:
                        obj_txt = "NIJE NADJEN"
                    print(f"[track_and_point] saka={hand_txt}  objekat={obj_txt}")
                    last_status = now

                if target is None:
                    time.sleep(0.15)
                    continue

                if now - last_nudge < interval_s:
                    time.sleep(0.05)
                    continue

                tx, ty, _r, _label, _score = target
                dx = (tx - w / 2) / (w / 2)
                dy = (ty - h / 2) / (h / 2)

                if abs(dx) < DEADZONE_FRAC and abs(dy) < DEADZONE_FRAC:
                    print(f"[track_and_point] objekat centriran (dx={dx:+.2f} dy={dy:+.2f}) - drzim pravac")
                    last_nudge = now
                    continue

                # Salji OBE ose odjednom (ne ili-ili) - svaka osa se
                # nudge-uje nezavisno ako prelazi svoj deadzone.
                if abs(dx) >= DEADZONE_FRAC:
                    screen_right = dx > 0
                    if SIGN_ABDUCT_MATCHES_SCREEN_RIGHT:
                        send_nudge("od_tela" if screen_right else "ka_telu", dry_run)
                    else:
                        send_nudge("ka_telu" if screen_right else "od_tela", dry_run)
                if abs(dy) >= DEADZONE_FRAC:
                    send_nudge("dole" if dy > 0 else "gore", dry_run)

                last_nudge = now
        except KeyboardInterrupt:
            print("\n[track_and_point] prekinuto")
        finally:
            cap.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("HAND_TRACKER_SOURCE", "0"))
    parser.add_argument("--object-label", default=None,
                         help="COCO kategorija za trazenje (npr. 'bottle', 'cup', "
                              "'cell phone'). Izostavi da vidis koje kategorije "
                              "model uopste prepoznaje u tvojoj sceni (prati ispis).")
    parser.add_argument("--score-threshold", type=float, default=0.5,
                         help="minimalna pouzdanost detekcije (0-1) - spusti na 0.3 ako model ne "
                              "nalazi male objekte, podigni na 0.7 ako hvata pogresne stvari")
    parser.add_argument("--model", choices=["lite0", "lite2"], default="lite0",
                         help="lite0 = brzi/manje tacan (default), lite2 = 448x448, znatno "
                              "tacniji za male/reflektujuce objekte (flasa, solja, telefon)")
    parser.add_argument("--interval", type=float, default=1.5,
                         help="sekunde izmedju uzastopnih nudge komandi")
    parser.add_argument("--execute", action="store_true",
                         help="stvarno salji nudge na robota (default: dry-run, samo ispis)")
    parser.add_argument("--http-port", type=int, default=None,
                         help="servira anotiran frejm preko HTTP-a na ovom portu (radi headless)")
    args = parser.parse_args()

    run(
        args.source,
        args.object_label,
        args.score_threshold,
        args.model,
        dry_run=not args.execute,
        interval_s=args.interval,
        http_port=args.http_port,
    )
