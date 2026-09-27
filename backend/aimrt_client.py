"""
Tanak adapter oko a2_motion.A2Motion - sav STVARNI RPC kod (URL-ovi, tacni
payload oblici, granice zglobova, dijagnostika) sada zivi u a2_motion.py
(fajl koji je Mihajlo poslao, vec testiran na pravom robotu). Ovaj fajl
samo prevodi taj API na imena metoda koja service.py vec ocekuje, da se
service.py NE MENJA.

Kljucne ispravke koje ovim dobijamo u odnosu na nas prethodni pokusaj:
- GetJointState zivi pod McDataService, ne McMotionService - nas raniji
  404 na "GetJoinState"/"GetJointState" je bio pogresan SERVIS, ne
  pogresno ime metode.
- SetAction payload je {"header":..., "command": {"action":..., "ext_action":""}}
  - nas raniji pokusaj {"header":..., "action":...} je bio bez "command"
  omotaca, otud "Http req deserialize failed."
- DUAL_ARM redosled [left x7, right x7] je POTVRDJEN (ne vise pretpostavka).
- Postoje single-arm McPlanningGroup_LEFT_ARM/RIGHT_ARM grupe.

NAMERNO NISAM dodao "ROUTE_TO_TARGET" (visestepeni prelaz kroz
McAction_RL_LOCOMOTION_DEFAULT pre cilja) koji si pomenuo u chatu - taj
isecak nije bio ni u jednom poslatom fajlu, pa ga tretiram kao
nepotvrdjen. ensure_arm_action() ispod radi TACNO ono sto a2_motion.py
vec radi (jednostepeni prelaz sa sigurnosnim proverama). Ako se na
robotu pokaze da direktan prelaz iz JOINT_SERVO ne uspeva, dodaj rutu
ovde - obelezio sam mesto.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# AIMDK_BASE_URL i dalje radi kao pre (npr. http://localhost:9000 za sim,
# http://192.168.100.100:56322 za pravi robot) - parsiramo ga OVDE i
# postavljamo A2_MC_IP/A2_MC_PORT PRE uvoza a2_motion, jer taj modul
# racuna svoje URL-ove kao module-level konstante tacno pri uvozu.
_base_url = os.getenv("AIMDK_BASE_URL", "http://192.168.100.100:56322")
_parsed = urlparse(_base_url)
os.environ.setdefault("A2_MC_IP", _parsed.hostname or "192.168.100.100")
os.environ.setdefault("A2_MC_PORT", str(_parsed.port or 56322))

import a2_motion as _a2  # noqa: E402  (mora ici posle env setdefault-a iznad)
from a2_motion import LEFT_ARM, RIGHT_ARM  # noqa: E402


class AimRTArmClient:
    def __init__(self, timeout_s: float = 6.0):
        self._mc = _a2.A2Motion(dry_run=False, timeout=timeout_s, verbose=False)

    # --- Gate 1 & 2 checks ---
    def get_work_mode(self) -> dict:
        return {"mode": self._mc.get_work_mode()}

    def get_action(self) -> dict:
        cur, status = self._mc.get_action()
        return {"info": {"current_action": cur, "status": status}}

    def ensure_arm_action(self, action: str | None, allow_switch: bool,
                           require_interface: str, force_passive: bool) -> dict:
        """Sigurniji ulaz od sirovog SetAction - proverava standing/passive
        gate (nikad ne ugasi noge dok robot stoji) i da li mod uopste nudi
        trazeni interfejs ('planner'/'servo'/'online') pre prelaska.

        # TODO ako direktan prelaz ovde ne uspe sa McAction_RL_WHOLE_BODY_EXT_JOINT_SERVO
        # kao pocetnim stanjem: ovde bi islo "ROUTE_TO_TARGET" visestepeno
        # rutiranje (npr. prvo McAction_RL_LOCOMOTION_DEFAULT, pa tek onda
        # ciljna akcija) - NIJE dodato, nepotvrdjeno, videti napomenu na
        # vrhu fajla.
        """
        previous = self._mc.ensure_arm_action(
            action=action, allow_switch=allow_switch,
            require_interface=require_interface, force_passive=force_passive,
        )
        return {"previous_action": previous}

    def try_set_action(self, action: str) -> dict:
        """Sirov, jednostepeni SetAction - ispravan payload sad dolazi iz
        a2_motion.py. Preferiraj ensure_arm_action() gore osim ako znas
        tacno sta radis (ova metoda NEMA standing/passive sigurnosnu
        proveru)."""
        result = self._mc.set_action(action)
        return {"result": result}

    # --- Joint state ---
    def get_joint_state(self) -> dict:
        js = self._mc.joint_state()
        joints = [float(js[n]["position"]) for n in LEFT_ARM + RIGHT_ARM]
        return {"joints": joints}

    # --- Motion ---
    def planning_move_dual_arm(
        self,
        left_joints: list[float],
        right_joints: list[float],
        velocity_scale: float = 0.1,
        acceleration_scale: float = 0.1,
    ) -> dict:
        names = LEFT_ARM + RIGHT_ARM
        joints = [*left_joints, *right_joints]
        task_id = self._mc.planning_move_joint(
            "McPlanningGroup_DUAL_ARM", joints,
            velocity_scale=velocity_scale, acceleration_scale=acceleration_scale,
            names=names,
        )
        return {"task_id": str(task_id), "state": "SENT"}

    def close(self) -> None:
        self._mc._session.close()
