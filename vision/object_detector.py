"""
Detekcija OPSTEG objekta po kategoriji (ne po boji) preko MediaPipe
ObjectDetector task-a (EfficientDet-Lite0, COCO 90 klasa - bottle, cup,
cell phone, book, chair, person, laptop, backpack, itd).

Model se automatski preuzima u vision/models/efficientdet_lite0.tflite
na prvo pokretanje (~14MB, treba internet samo tada).

Vraca isti (x, y, radius)-stil kao color_target.find_target (plus label
i score), da bi postojeci dx/dy racun u track_and_point.py/
point_at_object.py mogao da se koristi skoro nepromenjen.
"""
from __future__ import annotations

import os
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import ObjectDetector, ObjectDetectorOptions, RunningMode

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")

MODEL_VARIANTS = {
    "lite0": (
        "efficientdet_lite0.tflite",
        "https://storage.googleapis.com/mediapipe-models/object_detector/"
        "efficientdet_lite0/int8/1/efficientdet_lite0.tflite",
    ),
    "lite2": (
        "efficientdet_lite2.tflite",
        "https://storage.googleapis.com/mediapipe-models/object_detector/"
        "efficientdet_lite2/float16/latest/efficientdet_lite2.tflite",
    ),
}


def ensure_model(variant: str = "lite0") -> str:
    filename, url = MODEL_VARIANTS[variant]
    model_path = os.path.join(MODEL_DIR, filename)
    if not os.path.exists(model_path):
        os.makedirs(MODEL_DIR, exist_ok=True)
        print(f"[object_detector] preuzimam {variant} model u {model_path} ...")
        urllib.request.urlretrieve(url, model_path)
        print("[object_detector] model preuzet.")
    return model_path


def create_detector(label: str | None, score_threshold: float = 0.5, max_results: int = 5,
                     variant: str = "lite0") -> ObjectDetector:
    """label=None -> detektuje BILO KOJU od 90 COCO kategorija (korisno prvi
    put, da vidis koje labele model uopste prepoznaje u tvojoj sceni pre
    nego sto se fiksiras na jednu). label='bottle' itd -> filtrira SAMO tu
    kategoriju (tacan, case-sensitive COCO naziv - obicno lowercase engleski,
    npr. 'bottle', 'cup', 'cell phone', 'book', 'chair', 'person').

    variant='lite0' (default, brzi, manje tacan) ili 'lite2' (448x448,
    znatno tacniji za manje/reflektujuce objekte poput flase/solje/telefona,
    malo sporiji - probaj ovo ako lite0 ne pronalazi male objekte)."""
    model_path = ensure_model(variant)
    options = ObjectDetectorOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=RunningMode.IMAGE,
        max_results=max_results,
        score_threshold=score_threshold,
        category_allowlist=[label] if label else None,
    )
    return ObjectDetector.create_from_options(options)


def find_object(detector: ObjectDetector, frame_bgr):
    """Vraca (best, all_detections) gde je best (x, y, radius, label, score)
    najbolje-ocenjene detekcije, ili (None, all_detections) ako nista nije
    nadjeno (npr. filtrirano po category_allowlist a te kategorije nema u
    frejmu)."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(mp_image)

    if not result.detections:
        return None, result.detections

    best_det = max(result.detections, key=lambda d: d.categories[0].score if d.categories else 0.0)
    if not best_det.categories:
        return None, result.detections

    bb = best_det.bounding_box
    cx = bb.origin_x + bb.width / 2
    cy = bb.origin_y + bb.height / 2
    radius = max(bb.width, bb.height) / 2
    cat = best_det.categories[0]
    return (cx, cy, radius, cat.category_name, cat.score), result.detections
