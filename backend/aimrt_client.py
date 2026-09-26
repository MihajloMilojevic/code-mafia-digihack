"""
Tanak klijent oko AimDK HTTP-RPC-a. Radi identično protiv pravog robota
(http://192.168.100.100:56322) i protiv sim_server.py (http://localhost:9000)
- jedino se menja AIMDK_BASE_URL.

Potvrđeno na pravom robotu (curl test, 2026-09-26):
- McBaseService/GetWorkMode  -> {"mode": "ControlSource_SAFE"}
- McActionService/GetAction  -> {"info": {"current_action": "...", "status": "..."}}
- McMotionService/PlanningMove -> {"task_id":"0","state":"CommonState_SUCCESS"}
  (task_id je UVEK "0" - MC prosledjuje ka pnc_arm:56321 i ne vraca pravi id;
  ovo "SUCCESS" NE znaci da se ruka pomerila, samo da je zahtev primljen -
  vidi napomenu u chatu o "quiet failure" ponasanju AimDK-a)

NIJE potvrdjeno (nagadjanje / za testiranje):
- GetJoinState (dokument tako piše, moguc tipo za GetJointState) - probaj oba
- SetAction payload shape - "Http req deserialize failed" na prvi pokusaj,
  sto znaci metoda VEROVATNO postoji ali oblik JSON-a nije ovaj
"""
from __future__ import annotations

import os
import httpx


class AimRTArmClient:
    def __init__(self, base_url: str | None = None, timeout_s: float = 2.0):
        self.base_url = (base_url or os.getenv("AIMDK_BASE_URL", "http://192.168.100.100:56322")).rstrip("/")
        self._client = httpx.Client(timeout=timeout_s)

    def _rpc(self, service: str, method: str, payload: dict) -> dict:
        url = f"{self.base_url}/rpc/aimdk.protocol.{service}/{method}"
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    # --- Gate 1 & 2 checks ---
    def get_work_mode(self) -> dict:
        return self._rpc("McBaseService", "GetWorkMode", {"header": {}})

    def get_action(self) -> dict:
        return self._rpc("McActionService", "GetAction", {"header": {}})

    def try_set_action(self, action: str) -> dict:
        """NIJE POTVRDJENO da je ovo tacan payload - na pravom robotu vraca
        'Http req deserialize failed.' Ostavljeno da se lako menja/testira
        vise varijanti bez diranja ostatka koda."""
        return self._rpc(
            "McActionService", "SetAction",
            {"header": {"control_source": "ControlSource_SAFE"}, "action": action},
        )

    # --- Joint state ---
    def get_joint_state(self) -> dict:
        """Dokument pominje 'GetJoinState' (bez t) doslovno - probaj to prvo,
        pa GetJointState kao fallback ako prvo vrati 404."""
        try:
            return self._rpc("McMotionService", "GetJoinState", {"header": {}})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return self._rpc("McMotionService", "GetJointState", {"header": {}})
            raise

    # --- Motion ---
    def planning_move_dual_arm(
        self,
        left_joints: list[float],
        right_joints: list[float],
        velocity_scale: float = 0.1,
        acceleration_scale: float = 0.1,
    ) -> dict:
        # PRETPOSTAVKA: redosled [left x7, right x7] u nizu od 14 - NIJE
        # eksplicitno potvrdjeno u dokumentaciji, treba potvrditi vizuelno
        # (pomeri samo jedan joint jedne ruke i gledaj koja se pomera).
        joints = [*left_joints, *right_joints]
        payload = {
            "header": {"timestamp": {}, "control_source": "ControlSource_SAFE"},
            "group": "McPlanningGroup_DUAL_ARM",
            "mode": "McPlanningMode_DEFAULT",
            "target": {"type": "JOINT", "joints": joints},
            "param": {"velocity_scale": velocity_scale, "acceleration_scale": acceleration_scale},
        }
        return self._rpc("McMotionService", "PlanningMove", payload)

    def close(self):
        self._client.close()
