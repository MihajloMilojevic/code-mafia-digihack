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

## 3. Ceo stack preko docker-compose (sim + backend + frontend + hand-tracker)

```bash
docker compose up --build
```

Ovo pokreće:
- `sim` (9000) - lažni AimDK server
- `backend` (8000) - naš servis, podrazumevano uperen na `sim`
- `frontend` (8080) - nginx servira `frontend/index.html`
- `hand-tracker` - hand-tracking prototip, ispisuje komande SAMO u
  svoj log (`docker compose logs -f hand-tracker`), ne zove ništa

Otvori `http://localhost:8080` u browseru za UI.

Za pravi robot umesto simulacije, ili za telefon kao kameru umesto
default placeholder IP-ja, napravi `.env` fajl (kopija `.env.example`)
pored `docker-compose.yml` i tu promeni vrednosti - `docker compose up`
će ga automatski pokupiti.

### Telefon kao kamera (za hand-tracker)

**Android:** instaliraj besplatnu app "IP Webcam", pokreni server u
appu, uzmi prikazani URL (obično `http://<telefon-ip>:8080/video`),
stavi ga u `.env` kao `HAND_TRACKER_SOURCE`.

**iPhone:** instaliraj "Iriun Webcam" ili "EpocCam" - ove se prijavljuju
kao obična vebkamera na računaru (ne kao URL). Za taj slučaj **ne
pokrećeš** `hand-tracker` kroz docker-compose (kontejner ne vidi
vebkameru domaćina bez dodatnog device mapiranja) - umesto toga pokreni
ga direktno na računaru:

```bash
cd vision
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python hand_tracker.py --source 1   # probaj 0, 1, 2... dok ne nadjes telefon
```

Ovo takođe otvara prozor uživo (`cv2.imshow`) jer nije u kontejneru -
korisno da vizuelno potvrdiš da prati saku pre nego što veruješ
konzolnom ispisu.

### Šta hand-tracker NE radi (namerno, za sada)

Samo ispisuje `[hand_tracker] saka=(x,y) komanda=levo jacina=0.42` u
konzolu/log. Ne zove `/api/nudge`, ne dodiruje robota. Sledeći korak
kad ovo bude pouzdano na tvom telefonu/vebkameri: zameniti `print()`
jednim HTTP pozivom ka `backend`-u - tek onda zatvaramo petlju.

### Chest fisheye kamere (za kasnije, ne ovaj fajl)

Prave A2 kamere (`CHEST_LEFT/RIGHT_FISHEYE`) nisu dostupne preko
`cv2.VideoCapture` - idu preko ROS2 topic-a (domain 232), i nemamo
kalibracione parametre za fisheye distorziju. To je zaseban zadatak
(rclpy subscriber + undistort) za kad se prototip potvrdi da radi.
