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

# dokumentovani opsezi (radijani) - koristi se za kliring na frontend-u
JOINT_RANGES = {
    "shoulder_flex": (-2.91, 2.91),
    "shoulder_abduct": (-2.0, 2.0),   # tacan opseg nije dokumentovan, oprezna procena oko home=1.26
    "upper_arm_roll": (-3.14, 3.14),  # nije dokumentovano, PROVERI pre slanja necega ekstremnog
    "elbow_left": (-2.0, -0.03),
    "elbow_right": (0.03, 2.0),
    "wrist1": (-1.0, 1.0),   # paralelna veza - opsezi nisu dokumentovani, budi konzervativan
    "wrist2": (-1.0, 1.0),
    "wrist3": (-1.0, 1.0),
}

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


async def _poll_loop():
    while True:
        try:
            work_mode = await asyncio.to_thread(client.get_work_mode)
            action = await asyncio.to_thread(client.get_action)
            joints = await asyncio.to_thread(client.get_joint_state)
            _latest_state.update(
                connected=True,
                control_source=work_mode.get("mode"),
                current_action=action.get("info", {}).get("current_action"),
                joints=joints.get("joints"),
                last_error=None,
                updated_at=time.time(),
            )
        except Exception as exc:
            _latest_state.update(connected=False, last_error=str(exc), updated_at=time.time())
        await asyncio.sleep(0.3)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_poll_loop())


@app.get("/api/state")
async def get_state():
    return {**_latest_state, "joint_names": JOINT_NAMES, "joint_ranges": JOINT_RANGES}


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
    from aimrt_client import AimRTArmClient as _C  # samo da izbegnem cirkularni uvoz gore
    left = [0.0, 1.26, 0.0, -0.03, 0.0, 0.0, 0.0]
    right = [0.0, -1.26, 0.0, 0.03, 0.0, 0.0, 0.0]
    try:
        result = await asyncio.to_thread(client.planning_move_dual_arm, left, right, 0.08, 0.08)
        return {"status": "sent", "rpc_result": result}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
