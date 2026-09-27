"""
demo_help_stand.py - niz od 4 poze jedne ruke ("pomozi osobi da ustane"):
podigni ruku -> spusti ugao -> rotiraj dlan -> povuci uz telo i savij lakat
-> (na kraju) povratak na pocetnu pozu.

Gadja NAS arm_control_app backend (/api/state, /api/pose) - service.py,
NE robota direktno. Isti obrazac kao ostatak projekta.

ENTER izmedju svakog koraka - pritisni Enter da izvrsis sledeci pokret.
Ctrl+C u bilo kom trenutku vraca ruku na pocetnu pozu pre izlaska.

SVI parametri su imenovane promenljive ispod - menjaj slobodno, testiraj,
ponovi. Nista se ne kodira "tvrdo" u telu skripte.
"""
from __future__ import annotations

import math
import sys

import requests

# ============================================================
# PODESI OVDE
# ============================================================

BACKEND_URL = "http://192.168.2.50:8989"   # arm_control_app backend (service.py)
SIDE = "right"                           # "left" ili "right" - koja ruka radi demo
VELOCITY_SCALE = 0.12                    # brzina za sve korake (0.05 sporo .. 0.3 brze)

# Indeksi unutar 7-vrednosnog niza JEDNE ruke (isti redosled kao ceo projekat):
#   0 shoulder_flex   1 shoulder_abduct   2 upper_arm_roll
#   3 elbow           4 wrist1   5 wrist2   6 wrist3
IDX_FLEX, IDX_ABDUCT, IDX_ROLL, IDX_ELBOW = 0, 1, 2, 3

# --- Korak 1: podigni ruku u visini ramena (napred), dlan na dole ---
STEP1_FLEX_DEG = 90.0     # 90 = horizontalno napred (0 = ruka visi)
STEP1_ROLL_RAD = 1.4     # NIJE POTVRDJENO na robotu da li je ovo "dlan dole" -
                          # gledaj prvi Enter-pauzu i podesi predznak/iznos ako treba

# --- Korak 2: smanji ugao ruke sa 90 na 60 stepeni ---
STEP2_FLEX_DEG = 60.0
STEP2_ROLL_RAD = STEP1_ROLL_RAD   # dlan ostaje isti kao u koraku 1

# --- Korak 3: rotiraj dlan ---
# PRETPOSTAVKA: "okrene dlanovima na dole" u tvom opisu za korak 3 je verovatno
# htelo da kaze "na GORE" (klasican hvat ispod ruke da pomognes nekom da ustane -
# prilazis dlanom dole, pa okreces dlan gore da uhvatis). Ako si STVARNO mislio
# da ostane isto kao korak 1/2, samo stavi: STEP3_ROLL_RAD = STEP2_ROLL_RAD
STEP3_FLEX_DEG = STEP2_FLEX_DEG    # ugao ruke se ne menja u ovom koraku
STEP3_ROLL_RAD = -1.4               # pretpostavka: sad dlan na gore (suprotno od koraka 1)

# --- Korak 4: povuci ruku nazad uz telo, savij lakat (kao da vuce nesto) ---
STEP4_FLEX_DEG = 20.0       # blizu tela (0 = potpuno spustena)
STEP4_ROLL_RAD = STEP3_ROLL_RAD   # rotacija dlana ostaje ista tokom povlacenja
# elbow opseg iz a2_motion.py: levo (-2.0 .. -0.03), desno (0.03 .. 2.0).
# Vrednost ispod je "dobrano savijen lakat", ne skroz na granici - bezbednije za prvi test.
STEP4_ELBOW_RAD = 1.2 if SIDE == "right" else -1.2

# ============================================================
# KRAJ PODESAVANJA - ispod je logika, ne bi trebalo da menjas
# ============================================================

STEPS = [
    ("Korak 1: podigni ruku (90 stepeni), dlan na dole", STEP1_FLEX_DEG, STEP1_ROLL_RAD, None),
    ("Korak 2: smanji ugao na 60 stepeni", STEP2_FLEX_DEG, STEP2_ROLL_RAD, None),
    ("Korak 3: rotiraj dlan", STEP3_FLEX_DEG, STEP3_ROLL_RAD, None),
    ("Korak 4: povuci ruku uz telo, savij lakat", STEP4_FLEX_DEG, STEP4_ROLL_RAD, STEP4_ELBOW_RAD),
]


def get_current_pose() -> tuple[list[float], list[float]]:
    """Cita TRENUTNU pozu sa backend-a (GetJointState preko poll petlje) -
    koristi se kao 'pocetna tacka', ne hardkodovan HOME. Ista logika kao
    a2_motion.py: poza driftuje izmedju sesija, nikad joj ne veruj slepo."""
    resp = requests.get(f"{BACKEND_URL}/api/state", timeout=5)
    resp.raise_for_status()
    data = resp.json()
    joints = data.get("joints")
    if not joints or len(joints) != 14:
        raise RuntimeError(
            f"Nema validnog stanja zglobova sa backend-a (proveri da li je "
            f"service.py pokrenut i da li GetJointState radi): {data}"
        )
    return joints[:7], joints[7:]


def send_pose(left: list[float], right: list[float]) -> dict:
    resp = requests.post(
        f"{BACKEND_URL}/api/pose",
        json={
            "left": left,
            "right": right,
            "velocity_scale": VELOCITY_SCALE,
            "acceleration_scale": VELOCITY_SCALE,
        },
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  -> {result}")
    return result


def build_target(
    base_left: list[float], base_right: list[float],
    flex_deg: float, roll_rad: float, elbow_rad: float | None,
) -> tuple[list[float], list[float]]:
    """Svaki korak racuna se OD POCETNE poze (ne kumulativno od prethodnog
    koraka) - tako da svaka 'poza' iz tvog opisa ostaje nezavisno definisana."""
    left = list(base_left)
    right = list(base_right)
    active = right if SIDE == "right" else left

    active[IDX_FLEX] = math.radians(flex_deg)
    active[IDX_ROLL] = roll_rad
    if elbow_rad is not None:
        active[IDX_ELBOW] = elbow_rad

    return left, right


def wait_enter(msg: str) -> None:
    input(f"\n[ENTER da izvrsis: {msg}] ")


def main() -> None:
    print(f"Demo 'pomozi da ustane' - ruka: {SIDE}, backend: {BACKEND_URL}")
    print("Citam trenutnu pozu kao pocetnu tacku...")
    start_left, start_right = get_current_pose()
    print(f"  pocetna leva:  {[f'{v:+.3f}' for v in start_left]}")
    print(f"  pocetna desna: {[f'{v:+.3f}' for v in start_right]}")

    try:
        for label, flex_deg, roll_rad, elbow_rad in STEPS:
            wait_enter(label)
            left, right = build_target(start_left, start_right, flex_deg, roll_rad, elbow_rad)
            print(f">>> {label}")
            send_pose(left, right)

        wait_enter("Poslednji korak - povratak na pocetnu pozu")
        print(">>> Povratak na pocetnu pozu")
        send_pose(start_left, start_right)

    except KeyboardInterrupt:
        print("\n\nPrekinuto - vracam ruku na pocetnu pozu pre izlaska...")
        send_pose(start_left, start_right)
        sys.exit(130)

    print("\nGotovo.")


if __name__ == "__main__":
    main()
