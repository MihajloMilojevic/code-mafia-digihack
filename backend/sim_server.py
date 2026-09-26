"""
Lazni AimDK server - implementira ISTI URL/JSON ugovor kao pravi robot na
portu 56322 (McBaseService, McActionService, McMotionService), da bi se
backend/frontend mogli razvijati i testirati bez robota.

Pokretanje:
    pip install fastapi uvicorn
    python sim_server.py --port 9000

Onda u service.py (ili .env): AIMDK_BASE_URL=http://localhost:9000

Namerno simulira i "quiet failure" ponasanje koje je dokumentacija opisala:
ako je STRICT_GATE=true i trenutni current_action ne prihvata PlanningMove,
server vraca code:0/SUCCESS ali NE menja stanje zglobova - tacno kao sto
mentori kazu da pravi robot radi. Ovo ti omogucava da testiras da li tvoj
frontend/backend ispravno primeti razmimoilazenje izmedju "poslato" i
"stvarno stanje", pre nego sto to prvi put vidis uzivo na robotu.
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

# joint1..7 home vrednosti (isto sto smo koristili u wave_controller.py)
LEFT_HOME = [0.0, 1.26, 0.0, -0.03, 0.0, 0.0, 0.0]
RIGHT_HOME = [0.0, -1.26, 0.0, 0.03, 0.0, 0.0, 0.0]

_lock = Lock()
_state = {
    "control_source": "ControlSource_SAFE",
    "current_action": "McAction_RL_WHOLE_BODY_EXT_PLANNING_MOVE",
    "joints": [*LEFT_HOME, *RIGHT_HOME],   # 14 vrednosti: left x7, right x7
    "strict_gate": False,                   # /debug/strict_gate menja ovo
}


def _header_ok() -> dict:
    return {"code": "0", "msg": "called successfully.", "timestamp": {"seconds": str(int(time.time())), "nanos": 0}}


@app.post("/rpc/aimdk.protocol.McBaseService/GetWorkMode")
async def get_work_mode():
    with _lock:
        return {"header": _header_ok(), "mode": _state["control_source"]}


@app.post("/rpc/aimdk.protocol.McActionService/GetAction")
async def get_action():
    with _lock:
        return {
            "header": _header_ok(),
            "info": {"current_action": _state["current_action"], "ext_action": "", "status": "McActionStatus_RUNNING"},
        }


@app.post("/rpc/aimdk.protocol.McActionService/SetAction")
async def set_action(payload: dict):
    action = payload.get("action")
    with _lock:
        if action:
            _state["current_action"] = action
            return {"header": _header_ok(), "result": "ok"}
    return {"header": {"code": "400", "msg": "missing 'action'"}}


@app.post("/rpc/aimdk.protocol.McMotionService/GetJoinState")
async def get_joint_state():
    with _lock:
        return {"header": _header_ok(), "joints": list(_state["joints"])}


@app.post("/rpc/aimdk.protocol.McMotionService/PlanningMove")
async def planning_move(payload: dict):
    target = payload.get("target", {})
    joints = target.get("joints")
    with _lock:
        allowed = (not _state["strict_gate"]) or _state["current_action"].endswith("_PLANNING_MOVE")
        if allowed and joints and len(joints) == 14:
            _state["joints"] = list(joints)
        # AimDK stil: uvek "success", cak i kad tiho nista nije uradio
        return {"header": _header_ok(), "task_id": "0", "state": "CommonState_SUCCESS"}


# --- pomocni debug endpoint-i, NE postoje na pravom robotu, samo za sim ---
@app.post("/debug/strict_gate")
async def debug_strict_gate(payload: dict):
    with _lock:
        _state["strict_gate"] = bool(payload.get("enabled", False))
        return {"strict_gate": _state["strict_gate"]}


@app.post("/debug/reset")
async def debug_reset():
    with _lock:
        _state["joints"] = [*LEFT_HOME, *RIGHT_HOME]
        _state["current_action"] = "McAction_RL_WHOLE_BODY_EXT_PLANNING_MOVE"
        return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("SIM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
