"""
Nas servis - cist REST API za frontend, ispod haube zove AimDK preko
aimrt_client.py. Radi identicno protiv sim_server.py i protiv pravog robota,
zavisno samo od AIMDK_BASE_URL env promenljive.

Pokretanje (protiv simulacije):
    AIMDK_BASE_URL=http://localhost:9000 python service.py --port 8000

Pokretanje (protiv pravog robota, sa PC2 ili preko SSH tunela):
    AIMDK_BASE_URL=http://192.168.100.100:56322 python service.py --port 8000
"""
from __future__ import annotations

import argparse
import asyncio
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aimrt_client import AimRTArmClient

JOINT_NAMES = [
    "shoulder_flex", "shoulder_abduct", "upper_arm_roll",
    "elbow", "wrist1", "wrist2", "wrist3",
]

# Iz a2_motion.py (ARM_LIMITS) - potvrdjene granice koje pnc_arm's
# BoundsChecker stvarno primenjuje, NE URDF granice (koje su sire/drugacije).
# Abdukcija i lakat su ASIMETRICNI po strani - zato imaju _left/_right
# varijante, isto kao sto je vec bio slucaj za lakat.
JOINT_RANGES = {
    "shoulder_flex": (-2.91, 2.91),
    "shoulder_abduct_left": (-0.5236, 1.6581),
    "shoulder_abduct_right": (-1.6581, 0.5236),
    "upper_arm_roll": (-2.91, 2.91),
    "elbow_left": (-2.0, -0.03),
    "elbow_right": (0.03, 2.0),
    "wrist1": (-2.0, 2.0),
    "wrist2": (-2.0, 2.0),
    "wrist3": (-2.0, 2.0),
}

# Zglobovi koji imaju odvojen opseg po strani - koristi se i u /api/joint i
# da frontend zna koje opsege da trazi po strani (vidi /api/state ispod).
PER_SIDE_RANGE_JOINTS = {"shoulder_abduct", "elbow"}

# Home/prirodna-viseca poza iz a2_motion.py (HOME_LEFT/HOME_RIGHT) - koriste
# se samo kao POCETNA vrednost u UI-ju i kao fallback za /api/home. Sama
# a2_motion.py napominje da ova poza "driftuje" izmedju sesija - za pouzdan
# povratak kuci koristi zivo procitanu pozu (vidi "Sinhronizuj sa robotom"
# dugme na frontend-u), ne ovu konstantu.
HOME_LEFT = [0.00, 1.20, 0.02, -0.10, 1.60, 0.0, 0.0]
HOME_RIGHT = [0.00, -1.20, 0.04, 0.10, 1.60, 0.0, 0.0]

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = AimRTArmClient()

_latest_state: dict = {
    "connected": False,
    "control_source": None,
    "current_action": None,
    "joints": None,  # 14 vrednosti, [left x7, right x7]
    "last_error": None,
    "updated_at": 0,
}


class PoseRequest(BaseModel):
    left: list[float]   # 7 vrednosti
    right: list[float]  # 7 vrednosti
    velocity_scale: float = 0.1
    acceleration_scale: float = 0.1


class SingleJointRequest(BaseModel):
    side: str        # "left" | "right"
    joint_name: str  # jedno od JOINT_NAMES
    value: float
    velocity_scale: float = 0.1


class EnsureActionRequest(BaseModel):
    action: str | None = None            # None => a2_motion sam bira po stanju nogu
    require_interface: str = "planner"   # "planner" | "servo" | "online"
    force_passive: bool = False          # ne diraj ako ne znas sta radi - vidi a2_motion.py


async def _poll_loop():
    while True:
        control_source = None
        current_action = None
        joints = None
        errors: list[str] = []

        try:
            work_mode = await asyncio.to_thread(client.get_work_mode)
            control_source = work_mode.get("mode")
        except Exception as exc:
            errors.append(f"GetWorkMode: {exc}")

        try:
            action = await asyncio.to_thread(client.get_action)
            current_action = action.get("info", {}).get("current_action")
        except Exception as exc:
            errors.append(f"GetAction: {exc}")

        try:
            joint_resp = await asyncio.to_thread(client.get_joint_state)
            joints = joint_resp.get("joints")
        except Exception as exc:
            errors.append(f"GetJointState: {exc}")

        _latest_state.update(
            connected=control_source is not None,  # bar GetWorkMode je uspeo
            control_source=control_source,
            current_action=current_action,
            joints=joints,
            last_error="; ".join(errors) if errors else None,
            updated_at=time.time(),
        )
        await asyncio.sleep(0.3)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_poll_loop())


@app.get("/api/state")
async def get_state():
    return {**_latest_state, "joint_names": JOINT_NAMES, "joint_ranges": JOINT_RANGES,
            "per_side_range_joints": list(PER_SIDE_RANGE_JOINTS)}


@app.post("/api/pose")
async def send_pose(req: PoseRequest):
    if len(req.left) != 7 or len(req.right) != 7:
        return {"status": "error", "detail": "left i right moraju imati po 7 vrednosti"}
    try:
        result = await asyncio.to_thread(
            client.planning_move_dual_arm,
            req.left, req.right, req.velocity_scale, req.acceleration_scale,
        )
        return {"status": "sent", "rpc_result": result}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.post("/api/joint")
async def send_single_joint(req: SingleJointRequest):
    """Menja JEDAN zglob, drzeci ostale na poslednjem poznatom stanju
    (iz poll loop-a) - tako slajder za jedan joint ne resetuje ostatak ruke."""
    if req.joint_name not in JOINT_NAMES:
        return {"status": "error", "detail": f"nepoznat joint: {req.joint_name}"}
    if _latest_state["joints"] is None:
        return {"status": "error", "detail": "jos nemamo poslednje stanje zglobova (poll nije stigao)"}

    joints = list(_latest_state["joints"])
    idx = JOINT_NAMES.index(req.joint_name) + (0 if req.side == "left" else 7)

    range_key = f"{req.joint_name}_{req.side}" if req.joint_name in PER_SIDE_RANGE_JOINTS else req.joint_name
    lo, hi = JOINT_RANGES[range_key]
    if not (lo <= req.value <= hi):
        return {"status": "error",
                "detail": f"{req.joint_name} ({req.side}) = {req.value:+.4f} van opsega [{lo}, {hi}]"}

    joints[idx] = req.value
    left, right = joints[:7], joints[7:]
    try:
        result = await asyncio.to_thread(
            client.planning_move_dual_arm, left, right, req.velocity_scale, req.velocity_scale
        )
        return {"status": "sent", "rpc_result": result}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.post("/api/home")
async def go_home():
    try:
        result = await asyncio.to_thread(
            client.planning_move_dual_arm, HOME_LEFT, HOME_RIGHT, 0.08, 0.08
        )
        return {"status": "sent", "rpc_result": result}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.post("/api/ensure_arm_action")
async def ensure_arm_action(req: EnsureActionRequest):
    """Sigurno prebacuje MC u mod koji nudi trazeni interfejs (podrazumevano
    'planner', za PlanningMove) - proverava standing/passive gate iz
    a2_motion.py (nikad ne gasi noge dok robot stoji). Ovo je jednostepen
    prelaz (bez ROUTE_TO_TARGET rutiranja - vidi napomenu u aimrt_client.py)."""
    try:
        result = await asyncio.to_thread(
            client.ensure_arm_action, req.action, True, req.require_interface, req.force_passive
        )
        return {"status": "ok", **result}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
