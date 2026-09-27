"""
Zatvara petlju: kamera (HSV detekcija objekta preko color_target.py) ->
/api/nudge na arm_control_app -> ruka ROBOTA se pomera ka objektu.

NAMERNO samo "pokazuje" (centrira objekat u vidnom polju preko nudge
komandi), NE pokusava stvarno da dohvati/uhvati - chest fisheye kamere
nemaju dubinu, pa prava 3D IK ka tacki nije moguca sa onim sto trenutno
imamo. Ovo je 2D vizuelno servo-vodjenje: objekat udesno u slici -> mala
nudge komanda u tom pravcu -> proveri ponovo -> ponovi dok nije centriran.

NE prati ljudsku saku (to radi hand_tracker.py, za drugu svrhu) - ovde je
signal ciljanja CISTO pozicija objekta u odnosu na centar slike.

PODRAZUMEVANO JE DRY-RUN (samo ispisuje sta bi poslao ka /api/nudge) -
isti princip kao ostatak projekta (a2_arm_stretch.py itd). --execute
stvarno salje HTTP pozive.

NEPOZNANICE KOJE MORAS DA PROVERIS NA ROBOTU PRE --execute:
  - SIGN_ABDUCT (da li "objekat desno u slici" znaci "od_tela" ili
    "ka_telu" za ovu konkretnu ruku/kameru) - NIJE PROVERENO, pogadjanje.
    Pokreni sporo (--interval 2.0), gledaj par ciklusa, obrni SIGN_ABDUCT
    u kodu ako se ruka udaljava od cilja umesto da mu se priblizava.
  - Koja kamera zapravo puni --source (ako je CHEST_LEFT/RIGHT_FISHEYE,
    "levo/desno u slici" NIJE isto sto i "levo/desno iz ugla robota" -
    fisheye ima siroko vidno polje, nije jednostavna projekcija).
"""
from __future__ import annotations

import argparse
import os
import time

import cv2
import requests

from color_target import DEFAULT_HSV_LOWER, DEFAULT_HSV_UPPER, find_target, parse_hsv
from http_preview import publish_frame, start_http_preview

def _normalize_url(url: str) -> str:
    """Dodaje http:// ako fali sema - 'requests' bez sheme baca nejasno
    'No connection adapters were found' umesto razumljive greske."""
    if not url.startswith(("http://", "https://")):
        return f"http://{url}"
    return url


ARM_CONTROL_URL = _normalize_url(os.getenv("ARM_CONTROL_URL", "http://127.0.0.1:8000"))
DEADZONE_FRAC = 0.10     # unutar ovoga od centra = "vec pokazuje", ne salji nista
NUDGE_AMOUNT_RAD = 0.08  # mala i bezbedna - isti red velicine kao dosadasnji nudge testovi

# TODO: PROVERI na robotu - koja ruka pokazuje, i da li je znak abdukcije tacan
ACTING_SIDE = "right"
SIGN_ABDUCT_MATCHES_SCREEN_RIGHT = True  # ako se ruka udaljava umesto priblizava, obrni na False


def send_nudge(direction: str, dry_run: bool) -> None:
    print(f"[point_at_object] nudge -> side={ACTING_SIDE} direction={direction}"
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
            print(f"[point_at_object]   *** GRANICA DOSTIGNUTA *** {data.get('detail')}")
        else:
            print(f"[point_at_object]   -> HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[point_at_object]   nudge NIJE uspeo: {e}")


def run(source: str, hsv_lower, hsv_upper, dry_run: bool, interval_s: float,
        http_port: int | None) -> None:
    if http_port is not None:
        start_http_preview(http_port)

    from frame_source import open_source
    cap = open_source(source)

    print(f"[point_at_object] {'DRY-RUN (samo ispis)' if dry_run else '*** LIVE - salje na robota ***'}"
          f"  side={ACTING_SIDE}  interval={interval_s}s")
    if not dry_run:
        print("[point_at_object] Ctrl+C za prekid u svakom trenutku.")

    last_nudge = 0.0
    last_status_print = 0.0
    STATUS_INTERVAL_S = 1.0  # nezavisno od nudge intervala - da se vidi da petlja uopste radi
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[point_at_object] frejm nije procitan (los izvor kamere?), pokusavam dalje...")
                time.sleep(0.2)
                continue

            h, w = frame.shape[:2]
            target, _mask = find_target(frame, hsv_lower, hsv_upper)

            if http_port is not None:
                annotated = frame.copy()
                h_, w_ = annotated.shape[:2]
                cv2.circle(annotated, (w_ // 2, h_ // 2), 6, (255, 255, 255), 1)
                if target is not None:
                    tx_, ty_, r_ = target
                    cv2.circle(annotated, (int(tx_), int(ty_)), int(r_), (255, 200, 0), 2)
                    cv2.line(annotated, (w_ // 2, h_ // 2), (int(tx_), int(ty_)), (0, 165, 255), 2)
                else:
                    cv2.putText(annotated, "cilj nije pronadjen - proveri --target-lower/--target-upper",
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                publish_frame(annotated)

            now = time.time()
            if target is None:
                if now - last_status_print > STATUS_INTERVAL_S:
                    print("[point_at_object] cilj nije pronadjen - proveri HSV opseg "
                          "(pokreni 'hand_tracker.py --tune' da nadjes prave vrednosti)")
                    last_status_print = now
                time.sleep(0.15)
                continue

            if now - last_nudge < interval_s:
                if now - last_status_print > STATUS_INTERVAL_S:
                    tx, ty, _r = target
                    print(f"[point_at_object] cilj vidljiv na ({tx:.0f},{ty:.0f}) - "
                          f"cekam interval ({interval_s}s izmedju nudge-ova)")
                    last_status_print = now
                time.sleep(0.05)
                continue

            tx, ty, _r = target
            dx = (tx - w / 2) / (w / 2)  # -1..1, pozitivno = objekat desno u slici
            dy = (ty - h / 2) / (h / 2)  # -1..1, pozitivno = objekat dole u slici

            if abs(dx) < DEADZONE_FRAC and abs(dy) < DEADZONE_FRAC:
                print(f"[point_at_object] cilj centriran (dx={dx:+.2f} dy={dy:+.2f}) - drzim pravac")
                last_nudge = now
                last_status_print = now
                continue

            if abs(dx) > abs(dy):
                screen_right = dx > 0
                if SIGN_ABDUCT_MATCHES_SCREEN_RIGHT:
                    direction = "od_tela" if screen_right else "ka_telu"
                else:
                    direction = "ka_telu" if screen_right else "od_tela"
            else:
                direction = "dole" if dy > 0 else "gore"

            print(f"[point_at_object] objekat=({tx:.0f},{ty:.0f}) dx={dx:+.2f} dy={dy:+.2f}")
            send_nudge(direction, dry_run)
            last_nudge = now
    except KeyboardInterrupt:
        print("\n[point_at_object] prekinuto")
    finally:
        cap.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("HAND_TRACKER_SOURCE", "0"))
    parser.add_argument("--target-lower", default=",".join(map(str, DEFAULT_HSV_LOWER)))
    parser.add_argument("--target-upper", default=",".join(map(str, DEFAULT_HSV_UPPER)))
    parser.add_argument("--interval", type=float, default=1.5,
                         help="sekunde izmedju uzastopnih nudge komandi - drzi VISOKO "
                              "(1.5-2.0) dok prvi put proveravas SIGN_ABDUCT")
    parser.add_argument("--execute", action="store_true",
                         help="stvarno salji nudge na robota (default: dry-run, samo ispis)")
    parser.add_argument("--http-port", type=int, default=None,
                         help="servira anotiran frejm preko HTTP-a na ovom portu (radi headless)")
    args = parser.parse_args()

    run(
        args.source,
        parse_hsv(args.target_lower),
        parse_hsv(args.target_upper),
        dry_run=not args.execute,
        interval_s=args.interval,
        http_port=args.http_port,
    )
