# Arm Control App

Sopstveni servis + UI za direktnu kontrolu A2 Ultra ruke preko AimDK
HTTP-RPC-a, mimo Comtrade-ovog `robot_supervisor_v2`. Ne zaobilazi
Agibot-ov sopstveni AimDK/pnc_arm sloj - to je i dalje jedini put do
motora, samo je ovo drugi klijent tog istog RPC-a.

## 0. Inicijalizacija projekta (jednom)

```bash
cd arm-control-app/backend

# virtuelno okruzenje
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

Deaktiviraš okruzenje sa `deactivate` kad zavrsis; sledeci put samo
`source .venv/bin/activate` ponovo, bez reinstalacije.

## 1. Lokalni test bez robota (simulacija)

Otvori dva terminala, u oba prvo `source backend/.venv/bin/activate`.

```bash
# terminal 1 - lazni AimDK server
cd backend
python sim_server.py --port 9000
```

```bash
# terminal 2 - nas servis, uperen na simulaciju
cd backend
AIMDK_BASE_URL=http://localhost:9000 python service.py --port 8000
```

Otvori `frontend/index.html` direktno u browseru (dvoklik ili
`open frontend/index.html`). Pomeraj slajdere - skeleton bi trebalo
da se pomera odmah jer je simulacija bez gate provera (podrazumevano).

Da testiraš "quiet failure" ponašanje (mod ne prihvata PlanningMove):

```bash
curl -X POST http://localhost:9000/debug/strict_gate -d '{"enabled": true}' \
  -H 'Content-type: application/json'
```

Sad će `PlanningMove` i dalje vraćati `SUCCESS`, ali se `joints` u
`/api/state` neće menjati dok `current_action` ne završava na
`_PLANNING_MOVE` - to je tačno ponašanje koje smo videli na pravom
robotu i za koje treba da imaš plan (vizuelno upozorenje u UI-ju kad
se komandovano stanje razmimoiđe sa stvarnim - trenutno backend to ne
detektuje eksplicitno, samo prikazuje šta god `GetJoinState` vrati).

## 2. Test protiv pravog robota

```bash
cd backend
source .venv/bin/activate
AIMDK_BASE_URL=http://192.168.100.100:56322 python service.py --port 8000
```

Ako pokrećeš `service.py` na PC2 (a ne na svom laptopu), otvori
`frontend/index.html` preko browsera na istoj mašini, ili prosledi
port 8000 preko SSH tunela na svoj laptop:

```bash
ssh -J agi@192.168.100.100 -L 8000:localhost:8000 agi@192.168.100.110
```

pa otvori `frontend/index.html` lokalno sa `API` promenljivom u
JS-u podešenom na `http://localhost:8000` (trenutno je prazan string
`""`, radi samo ako frontend i backend dele isti origin).

## Poznate nepotvrđene stvari (proveri pre nego što veruješ UI-ju)

- Redosled 14 vrednosti u `PlanningMove` nizu (`[left×7, right×7]`) -
  pretpostavka, nije eksplicitno dokumentovano.
- `GetJoinState`/`GetJointState` - tačan naziv i response shape nisu
  potvrđeni na robotu (dokument piše "GetJoinState", ali može biti typo).
- `SetAction` payload - trenutni pokušaj (`{"action": "..."}`) je vraćao
  "Http req deserialize failed." na robotu - realan oblik nepoznat.
- Opsezi za `upper_arm_roll` i `wrist1-3` u `service.py` su procena,
  ne iz dokumentacije - ne veruj im slepo, kreći se u malim koracima.
