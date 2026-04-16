# Smart Gym Rep Counter & Form Checker

End-to-end wearable AI system: an **ESP32 + ADXL345** strapped to the forearm streams 50 Hz accelerometer data → a **CNN-LSTM deep learning model** (trained on the Microsoft RecoFit dataset, 94 subjects, arm-worn sensors) classifies reps and scores form → a **React dashboard** shows live stats and lets you ask natural-language questions about your workouts.

**Model accuracy: 98.34%** on 50,156 validation windows (real human subjects).

---

## Architecture

```
 ┌──────────────┐    HTTP JSON     ┌──────────────────────┐    SQLite
 │ ESP32+ADXL345│ ────────────────▶│ FastAPI backend      │──────────▶  sessions.db
 │  forearm     │  /api/ingest     │ - JWT auth           │
 │  50 Hz       │  every 0.5s      │ - CNN-LSTM model     │
 └──────────────┘                  │ - rep counter / form │
                                   │ - /api/query/stream  │──┐
                                   └──────────┬───────────┘  │
                                              │ REST          │ HTTP
                                              ▼               ▼
                                   ┌──────────────────┐   ┌──────────────┐
                                   │ React + Tailwind │   │ Ollama LLM   │
                                   │  LiveCounter     │   │ (local, free)│
                                   │  SessionHistory  │   └──────────────┘
                                   │  FloatingChat    │
                                   └──────────────────┘
```

---

## Supported Exercises

The model classifies **3 classes** based on forearm accelerometer patterns:

| Label | Exercises that map to it |
|---|---|
| **curl** | Bicep curl (dumbbell, barbell, cable, band), hammer curl, concentration curl |
| **squat** | Bodyweight squat, goblet squat, jump squat, sumo squat |
| **rest** | Any stationary position — arm at side, on lap, on table |

> Only these 3 classes. Pushups, rows, shoulder press etc. will be misclassified — train a custom model (see below) to add more.

---

## Sensor Placement — Critical for Accuracy

**The model was trained on arm-worn sensors (Microsoft RecoFit dataset). Wrong placement = wrong predictions.**

### Where to strap it

```
                    CORRECT placement
                    ─────────────────
          shoulder
              │
              │   ← upper arm
              │
         [elbow]
              │
        ══════╪══════  ← ADXL345 strapped here
              │         middle of FOREARM (inner side)
              │         Z-axis pointing AWAY from skin
              │
          [wrist]
              │
           hand
```

**Rules:**
- Strap to the **middle of the forearm** (between elbow and wrist), inner side (palm-facing side)
- Sensor flat against skin, **Z-axis perpendicular to arm** (pointing outward, away from skin)
- **NOT** on the wrist bone — too much rotation noise
- **NOT** on the upper arm — different motion signature
- Use a snug velcro strap — it must not slide during reps

### Axis orientation expected by model

```
     Forearm (right arm, palm up)

     ──────── Z ────────►  (pointing up, away from skin)
         │
         │  Y  (pointing toward thumb/radial side)
         │
         ▼
         X  (pointing toward fingers, along arm length)
```

---

## Performing Exercises for Best Results

### Bicep Curl
- Keep **elbow pinned to your side** — don't swing the arm out
- Full range: fully extend at bottom, curl to shoulder at top
- Pace: ~2 seconds up, 2 seconds down (too fast = misses reps)
- Do NOT let the wrist rotate excessively during the curl

### Squat
- Hold the **sensor arm straight at your side** during the squat
- Do NOT swing your arms forward — it looks like a curl to the model
- Pace: ~2 seconds down, 2 seconds up
- Full depth squat preferred over half-squat (larger acceleration signature)

### Rest
- Let the arm hang naturally at your side, or rest it on your thigh
- Stay still — even gentle fidgeting can trigger false reps

---

## Quick Start (Docker — recommended)

```bash
cp .env.example .env
docker compose up --build
```

Opens at:
- **Frontend**: http://localhost:5173 → login as `admin` / `admin`
- **Backend API**: http://localhost:8001/api/health
- **API docs**: http://localhost:8001/docs

> Port 8001 is used for the backend host port (8000 inside the container).

---

## Quick Start (Local — no Docker)

Requires Python 3.11+, Node 18+.

```bash
# First time: create venv and install deps
python3 -m venv ml_env
ml_env/bin/pip install "tensorflow[and-cuda]" fastapi "uvicorn[standard]" \
    pydantic PyJWT anthropic python-multipart numpy scipy pandas scikit-learn

cd frontend && npm install && cd ..

# Every time
bash start_local.sh
```

Opens at http://localhost:5173 (backend on :8001).

---

## Wiring (ESP32 + ADXL345)

```
     ESP32 DevKit                         ADXL345
  ┌──────────────┐                    ┌─────────────┐
  │              │                    │             │
  │ 3V3 ─────────┼────────────────────┼─ VCC        │
  │ GND ─────────┼────────────────────┼─ GND        │
  │ GPIO21 (SDA)─┼────────────────────┼─ SDA        │
  │ GPIO22 (SCL)─┼────────────────────┼─ SCL        │
  │              │  SDO→GND ⇒ addr 0x53             │
  └──────────────┘                    └─────────────┘
                                  CS pin → 3V3 (I²C mode)
```

Firmware configured for: **Full resolution, ±4g range, 50 Hz sampling**.
Outputs: g-units (gravity = ~1.0). Matches RecoFit training data exactly.

---

## Hardware Setup

### 1. Find your machine's LAN IP

```bash
ip route get 1 | awk '{print $7; exit}'    # Linux
ipconfig getifaddr en0                      # macOS
```

### 2. Edit `firmware/main/main.ino`

```cpp
#define WIFI_SSID   "YourWiFiName"
#define WIFI_PASS   "YourWiFiPassword"
#define SERVER_URL  "http://192.168.1.XX:8001/api/ingest"   // ← port 8001!
#define DEVICE_ID   "my-wrist-01"
```

> **Important:** The port is **8001**, not 8000.

### 3. Upload via Arduino IDE

- Board: **ESP32 Dev Module**
- Install dependency: Sketch → Include Library → Manage Libraries → search **Adafruit ADXL345**
- Upload, open Serial Monitor at **115200 baud**

Expected output:
```
WiFi OK, IP=192.168.1.57
Ready.
POST 200  {"exercise":"rest","confidence":0.99,"reps":0,...}
POST 200  {"exercise":"curl","confidence":0.97,"reps":1,...}
```

---

## Testing Without Hardware

### A. Frontend mock mode (zero setup)

Auth screen → **Connection settings** → **Switch to mock**. Fakes live data, history, and chat.

### B. Simulator → real backend

```bash
# Streams synthetic curl/squat/rest windows to the backend
python tools/simulate_device.py --loop --speed 0.5
```

### C. Test inference directly with curl

```bash
# Get token
TOKEN=$(curl -sX POST http://localhost:8001/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# Check model mode (should say "keras" not "heuristic")
curl -s http://localhost:8001/api/health
```

### D. Test with real RecoFit data (verifies model is working)

```bash
python3 -c "
import scipy.io, numpy as np, requests, json

mat = scipy.io.loadmat('ml/datasets/recofit_single.mat')
sd = mat['subject_data']

# Real bicep curl window
accel = sd[1, 4][0,0]['data'][0,0]['accelDataMatrix']
w = accel[len(accel)//2 : len(accel)//2+50, 1:4]
r = requests.post('http://localhost:8001/api/ingest',
    json={'ax': w[:,0].tolist(), 'ay': w[:,1].tolist(), 'az': w[:,2].tolist(),
          'device_id': 'test'})
print('Curl:', r.json()['exercise'], f\"{r.json()['confidence']*100:.0f}%\")

# Real squat window
accel2 = sd[2, 50][0,0]['data'][0,0]['accelDataMatrix']
w2 = accel2[len(accel2)//2 : len(accel2)//2+50, 1:4]
r2 = requests.post('http://localhost:8001/api/ingest',
    json={'ax': w2[:,0].tolist(), 'ay': w2[:,1].tolist(), 'az': w2[:,2].tolist(),
          'device_id': 'test'})
print('Squat:', r2.json()['exercise'], f\"{r2.json()['confidence']*100:.0f}%\")
"
# Expected: Curl: curl 100%   Squat: squat 100%
```

---

## Diagnosing Poor Performance

### Step 1 — Confirm the model is loaded

```bash
curl -s http://localhost:8001/api/health
# Must show: "model": "keras"
# If "model": "heuristic" → model.h5 missing, see Training section
```

### Step 2 — Check sensor unit output

On Arduino Serial Monitor, the firmware prints `POST 200 {...}`. Look at the response:
```json
{"exercise": "rest", "confidence": 0.99, ...}
```
If confidence is always low (<0.7) on everything, the sensor axes may be inverted or the scale is wrong.

**Expected values at rest** (arm hanging at side):
- `ax` ≈ 0.0 (along arm, no gravity)
- `ay` ≈ 0.0 (across arm, no gravity)
- `az` ≈ +1.0 (perpendicular to skin, gravity component)

### Step 3 — Common problems and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Always predicts `rest` | Sensor on wrong body part / too much noise | Move to forearm middle, tighten strap |
| Curl detected as `squat` | Wrong axis orientation | Rotate sensor 90°; Z must point away from skin |
| Low confidence (<0.7) on every window | Sensor scale wrong | Check DATAFMT register; LSB_PER_G must be 256 for ±4g full-res |
| Reps not counting | Forgot to press **Start Session** in UI | Click Start Session before exercising |
| Model shows `heuristic` in health | `model.h5` not found by backend | Check `MODEL_PATH` env var or volume mount |
| Correct exercise, reps still wrong | Too fast or too slow | Aim for 2s/rep; hold still for 2s between sets |
| Good accuracy in test D above, bad with hardware | Sensor placement or axis orientation mismatch | Follow sensor placement guide above exactly |

### Step 4 — Collect your own data + retrain

If the model still underperforms on your specific sensor/body, collect 90 seconds per class and retrain:

```bash
# 1. Get a token
TOKEN=$(curl -sX POST http://localhost:8001/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 2. Collect data (repeat for curl, squat, rest)
curl -sX POST http://localhost:8001/api/collect/start \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"label":"curl"}'
# ... do curls for 90 seconds ...
curl -sX POST http://localhost:8001/api/collect/stop -H "Authorization: Bearer $TOKEN"

# 3. Retrain on GPU (takes ~25 min)
cp data/collected_dataset.csv ml/my_data.csv
bash ml/train_gpu.sh --csv ml/my_data.csv --epochs 50

# 4. Restart backend to load new model
docker compose restart backend   # or: restart start_local.sh
```

---

## Model Details

| Property | Value |
|---|---|
| Architecture | CNN-LSTM hybrid (4 CNN + 2 BiLSTM layers) |
| Parameters | 540,739 (~2 MB) |
| Training dataset | Microsoft RecoFit (94 subjects, arm-worn, 50 Hz) |
| Training windows | 250,788 (after 3× augmentation) |
| Val accuracy | **98.34%** |
| Curl F1 | 0.973 |
| Squat F1 | 0.961 |
| Rest F1 | 0.990 |
| Inference speed | ~1 ms/window on CPU |
| Input | 50 samples × 3 axes (1 second at 50 Hz), z-score normalised |

Confusion matrix (50,156 validation samples):
```
              curl    squat     rest
    curl:     5578       63       81   (97% correct)
   squat:       67     7916      234   (96% correct)
    rest:      103      283    35831   (99% correct)
```

### Retraining on GPU (RTX 4050)

```bash
# Dataset already at ml/datasets/recofit_single.mat (1.5 GB)
bash ml/train_gpu.sh --epochs 50 --batch 64

# Force re-download dataset if needed:
bash ml/train_gpu.sh --download --epochs 50
```

Training time: ~25 minutes on RTX 4050 (30s/epoch × 50 epochs).

---

## Repo Layout

```
.
├── firmware/main/main.ino         ESP32 + ADXL345 sketch (50 Hz, sliding window)
├── ml/
│   ├── train_model.py             CNN-LSTM trainer — RecoFit .mat or legacy CSV
│   ├── train_gpu.sh               GPU launch wrapper (sets LD_LIBRARY_PATH)
│   ├── collect_data.py            Stand-alone data collector server
│   ├── sample_dataset.csv         20 synthetic rows (pipeline smoke-test)
│   ├── training_results.md        Last training run stats
│   └── datasets/
│       └── recofit_single.mat     Microsoft RecoFit dataset (1.5 GB, 94 subjects)
├── backend/
│   ├── server.py                  FastAPI: ingest / sessions / query / collect / auth
│   ├── model.h5                   Trained Keras model (6.3 MB, 98.34% accuracy)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                      React + Vite + TS + Tailwind + Recharts
│   ├── src/components/
│   │   ├── AuthPage.tsx           Login + advanced settings
│   │   ├── LiveCounter.tsx        Live exercise + reps + form bar
│   │   ├── SessionHistory.tsx     Past sessions + Recharts charts
│   │   └── FloatingChat.tsx       Streaming LLM chat widget
│   ├── nginx.conf                 SPA + /api proxy to backend
│   └── Dockerfile
├── tools/
│   └── simulate_device.py        Fake ESP32 — streams synthetic windows
├── docker-compose.yml             Backend :8001, Frontend :5173
├── start_local.sh                 One-command local run (no Docker)
├── Makefile
└── .env.example
```

---

## NLP Coach (Floating Chat)

The chat widget answers questions about your workout history:
- *"How many reps did I do yesterday?"*
- *"Which exercise had the worst form this week?"*
- *"Am I improving?"*

**LLM priority chain:** Ollama (local, free) → Anthropic Claude → rule-based fallback.

### Install Ollama (optional but recommended)

```bash
# Ubuntu / Debian
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:1b       # ~1.3 GB, fast

# Make sure Ollama binds to all interfaces (needed for Docker)
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Then set in `.env`:
```
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.2:1b
```

---

## API Reference

JWT routes need `Authorization: Bearer <token>`. `/api/ingest` is public (rate-limited).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/signup` | — | Create account |
| POST | `/api/auth/login` | — | Get JWT token |
| GET | `/api/auth/me` | ✓ | Current user |
| POST | `/api/ingest` | — | Classify 50-sample window, bump rep counter |
| POST | `/api/session/start` | ✓ | Begin tracking |
| POST | `/api/session/end` | ✓ | Finalize session |
| GET | `/api/session/{id}` | ✓ | Session detail |
| GET | `/api/sessions` | ✓ | Session list |
| GET | `/api/live` | ✓ | Current rep/form snapshot (poll 500ms) |
| POST | `/api/query` | ✓ | NLP coach (non-streaming) |
| POST | `/api/query/stream` | ✓ | NLP coach (streaming ndjson) |
| POST | `/api/collect/start` | ✓ | Start labelling windows |
| POST | `/api/collect/stop` | ✓ | Stop labelling |
| GET | `/api/collect/status` | ✓ | Label + window counts |
| GET | `/api/health` | — | Liveness + model mode |

---

## Make Targets

| Command | What it does |
|---|---|
| `make up` | Build + run (detached) |
| `make down` | Stop containers |
| `make logs` | Tail logs |
| `make rebuild` | Full rebuild + recreate |
| `make clean` | Stop + delete volumes + sessions.db |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `"model":"heuristic"` in `/api/health` | `model.h5` missing — train or check MODEL_PATH |
| Always predicts `rest` | Sensor in wrong place — see Sensor Placement section |
| `ESP32 WiFi FAILED` | Wrong SSID/PW or 5 GHz network (ESP32 = 2.4 GHz only) |
| `POST failed` on ESP32 | Wrong IP or port in SERVER_URL — must be `:8001` |
| Reps not counting | Click **Start Session** in UI before exercising |
| Low confidence on all classes | Check sensor g-unit conversion (LSB_PER_G = 256) |
| Port 8001 already in use | `fuser -k 8001/tcp` then `docker compose up` |
| Chat returns garbage | LLM too small — `ollama pull llama3.2:3b` |
| `HTTP 429` from `/api/ingest` | Rate limit hit — raise `RATE_LIMIT_PER_MIN` in server.py |
