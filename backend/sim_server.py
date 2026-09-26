"""
Lazni AimDK server - AZURIRAN da prati STVARNI ugovor iz a2_motion.py
(potvrdjeno na pravom robotu), ne nasa ranija nagadjanja. Promene u
odnosu na prethodnu verziju:
  - GetJointState je sad pod McDataService (ne McMotionService), i vraca
    "states": [{"name","position","velocity","effort"}, ...] - ne flat
    listu brojeva.
  - SetAction prihvata pravi nested payload: {"command": {"action":...,
    "ext_action": ...}}, ne flat {"action": ...}.

Pokretanje:
    pip install -r requirements.txt
    python sim_server.py --host 0.0.0.0 --port 9000
"""
from __future__ import annotations

import argparse
import os
import time
from threading import Lock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Isti imenski obrazac kao a2_motion.py (LEFT_ARM/RIGHT_ARM) - dupliran ovde
# namerno da sim ostane nezavisan od a2_motion.py kao samostalan fake server.
LEFT_ARM = [f"idx{n:02d}_left_arm_joint{i}" for i, n in enumerate(range(13, 20), start=1)]
RIGHT_ARM = [f"idx{n:02d}_right_arm_joint{i}" for i, n in enumerate(range(20, 27), start=1)]

# HOME_LEFT/RIGHT iz a2_motion.py (7 vrednosti po ruci)
HOME_LEFT = [0.00, 1.20, 0.02, -0.10, 1.60, 0.0, 0.0]
HOME_RIGHT = [0.00, -1.20, 0.04, 0.10, 1.60, 0.0, 0.0]

_lock = Lock()
_state = {
    "control_source": "ControlSource_SAFE",
    "current_action": "McAction_RL_WHOLE_BODY_EXT_PLANNING_MOVE",
    "joints": {**dict(zip(LEFT_ARM, HOME_LEFT)), **dict(zip(RIGHT_ARM, HOME_RIGHT))},
    "strict_gate": False,  # /debug/strict_gate menja ovo
}

# Isti PLANNER_ACTIONS koncept kao a2_motion.py - koristi se samo da
# strict_gate zna kad da "tiho" odbije PlanningMove.
PLANNER_ACTION_SUFFIX = "_PLANNING_MOVE"


def _header_ok() -> dict:
    return {"code": "0", "msg": "called successfully.",
            "timestamp": {"seconds": str(int(time.time())), "nanos": 0}}


@app.post("/rpc/aimdk.protocol.McBaseService/GetWorkMode")
async def get_work_mode():
    with _lock:
        return {"header": _header_ok(), "mode": _state["control_source"]}


@app.post("/rpc/aimdk.protocol.McBaseService/GetState")
async def get_state():
    with _lock:
        return {"header": _header_ok(), "state": {
            "work_state": "McWorkState_ENABLED",
            "robot_init_state": "McRobotInitState_STAND_READY",
            "is_walking": False,
            "is_collisioned": False,
            "control_info": {"mode": _state["control_source"]},
            "action_info": {"current_action": _state["current_action"]},
        }}


@app.post("/rpc/aimdk.protocol.McActionService/GetAction")
async def get_action():
    with _lock:
        return {
            "header": _header_ok(),
            "info": {"current_action": _state["current_action"], "ext_action": "",
                      "status": "McActionStatus_RUNNING"},
        }


@app.post("/rpc/aimdk.protocol.McActionService/GetAvailableCommands")
async def get_available_commands():
    # u simulaciji dozvoli sve poznate arm-capable akcije - stvarni robot
    # ovo ogranicava po firmveru/konfiguraciji
    from a2_motion import ARM_CAPABLE_ACTIONS
    return {"header": _header_ok(),
            "commands": [{"action": a} for a in ARM_CAPABLE_ACTIONS]}


@app.post("/rpc/aimdk.protocol.McActionService/SetAction")
async def set_action(payload: dict):
    # PRAVI oblik: {"header":..., "command": {"action": "...", "ext_action": ""}}
    command = payload.get("command", {})
    action = command.get("action")
    with _lock:
        if action:
            _state["current_action"] = action
            return {"header": _header_ok()}
    return {"header": {"code": "400", "msg": "missing command.action"}}


@app.post("/rpc/aimdk.protocol.McDataService/GetJointState")
async def get_joint_state():
    with _lock:
        states = [
            {"name": name, "position": pos, "velocity": 0.0, "effort": 0.0}
            for name, pos in _state["joints"].items()
        ]
        return {"header": _header_ok(), "states": states}


@app.post("/rpc/aimdk.protocol.McMotionService/PlanningMove")
async def planning_move(payload: dict):
    target = payload.get("target", {})
    joints = target.get("joints")
    group = payload.get("group", "")
    with _lock:
        allowed = (not _state["strict_gate"]) or _state["current_action"].endswith(PLANNER_ACTION_SUFFIX)
        if allowed and joints:
            if group == "McPlanningGroup_DUAL_ARM" and len(joints) == 14:
                names = LEFT_ARM + RIGHT_ARM
            elif group == "McPlanningGroup_LEFT_ARM" and len(joints) == 7:
                names = LEFT_ARM
            elif group == "McPlanningGroup_RIGHT_ARM" and len(joints) == 7:
                names = RIGHT_ARM
            else:
                names = []
            for n, v in zip(names, joints):
                _state["joints"][n] = v
        # AimDK stil: uvek "success", cak i kad tiho nista nije uradio.
        # task_id je UVEK "0" na pravom robotu (MC prosledjuje ka pnc_arm i
        # ne vraca pravi id) - simuliramo isto ponasanje.
        return {"header": _header_ok(), "task_id": "0"}


# --- pomocni debug endpoint-i, NE postoje na pravom robotu, samo za sim ---
@app.post("/debug/strict_gate")
async def debug_strict_gate(payload: dict):
    with _lock:
        _state["strict_gate"] = bool(payload.get("enabled", False))
        return {"strict_gate": _state["strict_gate"]}


@app.post("/debug/reset")
async def debug_reset():
    with _lock:
        _state["joints"] = {**dict(zip(LEFT_ARM, HOME_LEFT)), **dict(zip(RIGHT_ARM, HOME_RIGHT))}
        _state["current_action"] = "McAction_RL_WHOLE_BODY_EXT_PLANNING_MOVE"
        return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("SIM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
