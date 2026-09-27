"""
Jedinstven interfejs za izvor kamere - .read() -> (ok, frame_bgr), .release().
Isti obrazac za sve tri vrste izvora, da hand_tracker.py/track_and_point.py/
point_at_object.py ne moraju da znaju razliku.

Izvori (--source):
  "0", "1", ...              -> cv2.VideoCapture(index) - vebkamera
  "http://..."               -> cv2.VideoCapture(url) - telefon (IP Webcam app)
  "ros2:left" / "ros2:right" -> ROS2 (CHEST_LEFT/RIGHT_FISHEYE), preko
                                 VENDOROVANOG ros2_capture.py (Ros2VideoCapture)
                                 - taj fajl je PRAVI, vec testiran kod iz
                                 robot_services/vision/detection/, ne moje
                                 nagadjanje. Ima ispravan QoS profil
                                 (BEST_EFFORT/TRANSIENT_LOCAL) koji je
                                 razlog zasto moj raniji pokusaj (bez QoS-a)
                                 nikad nije primio nijedan frejm.

VAZNO za ros2:* izvore: MORA se pokrenuti u okruzenju gde je ROS2/rclpy
VEC instaliran (isto okruzenje kao postojeci robot_services/vision na PC2)
- rclpy NIJE pip paket koji ide u nas obican venv.
"""
from __future__ import annotations

import cv2

from ros2_capture import Ros2VideoCapture


class CV2FrameSource:
    def __init__(self, source: str):
        cap_source = int(source) if source.isdigit() else source
        self.cap = cv2.VideoCapture(cap_source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Ne mogu da otvorim izvor kamere: {source}")

    def read(self):
        return self.cap.read()

    def release(self):
        self.cap.release()


class ROS2FrameSource:
    """Tanak omotac oko PRAVOG Ros2VideoCapture (vendorovan iz
    ros2_capture.py) - alias 'left'/'right' -> CHEST_LEFT/RIGHT_FISHEYE."""

    _ALIAS = {"left": "CHEST_LEFT_FISHEYE", "right": "CHEST_RIGHT_FISHEYE"}

    def __init__(self, side: str):
        if side not in self._ALIAS:
            raise ValueError(f"nepoznata strana '{side}', ocekujem 'left' ili 'right'")
        # Ros2VideoCapture sam radi configure_ros_environment_defaults()
        # (ROS_DOMAIN_ID/FASTRTPS profil) i baca jasan RuntimeError sa
        # timeout-om ako frejm ne stigne za 10s - ne treba nam sopstvena
        # cekaj-i-upozori logika kao u mom prethodnom pokusaju.
        self._cap = Ros2VideoCapture(self._ALIAS[side])

    def read(self):
        return self._cap.read()

    def release(self):
        self._cap.release()


def open_source(source: str):
    """Fabrika - vraca objekat sa .read()/.release(), isti interfejs bez
    obzira na stvarni izvor. 'ros2:left'/'ros2:right' za AIMA fisheye,
    sve ostalo ide na cv2.VideoCapture (vebkamera/telefon)."""
    if source.startswith("ros2:"):
        return ROS2FrameSource(source.split(":", 1)[1])
    return CV2FrameSource(source)
