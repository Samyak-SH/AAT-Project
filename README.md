# Smart Gym Rep Counter & Form Checker

End-to-end system that watches an ESP32 + ADXL345 wrist sensor, classifies
**bicep curls / squats / rest** with a 1-D CNN, counts reps, scores form
per-set, stores sessions in SQLite, and exposes a React dashboard with a
**floating streaming chat** powered by a **local LLM** (Ollama, no API keys).

## Architecture

```
 ┌──────────────┐    HTTP JSON     ┌──────────────────────┐    SQLite
 │ ESP32+ADXL345│ ────────────────▶│ FastAPI backend      │──────────▶  sessions.db
 │  firmware    │  /api/ingest     │ - JWT auth (users)   │
 │  (raw axes)  │  ~2 Hz windows   │ - Keras 1-D CNN      │
 └──────────────┘                  │ - rep counter / sets │
                                   │ - /api/query/stream  │──┐
                                   │   (ndjson tokens)    │  │
                                   └──────────┬───────────┘  │
                                              │ REST          │ HTTP
                                              ▼               ▼
                                   ┌──────────────────┐   ┌──────────────┐
                                   │ React + Tailwind │   │ Ollama on    │
                                   │  AuthPage        │   │ host (local  │
                                   │  LiveCounter     │   │ llama3.2)    │
                                   │  SessionHistory  │   └──────────────┘
                                   │  FloatingChat    │
                                   └──────────────────┘
```

## Wiring (ASCII)

```
         ESP32 DevKit                         ADXL345
      ┌──────────────┐                    ┌─────────────┐
      │              │                    │             │
      │ 3V3 ─────────┼────────────────────┼─ VCC        │
      │ GND ─────────┼────────────────────┼─ GND        │
      │ GPIO21 (SDA)─┼────────────────────┼─ SDA        │
      │ GPIO22 (SCL)─┼────────────────────┼─ SCL        │
      │              │  SDO→GND ⇒ addr 0x53│             │
      └──────────────┘                    └─────────────┘
                                      (leave CS pulled HIGH for I²C)
```

Mount the board to the wrist with the Z-axis pointing away from the skin; the
default heuristic (used when no Keras model is loaded) assumes that mounting.

## Repo layout

```
.
├── firmware/main/main.ino         ESP32 Arduino sketch (50Hz, sliding window)
├── ml/
│   ├── collect_data.py            Stand-alone collector (alternative to /api/collect)
│   ├── train_model.py             1-D CNN in Keras → model.h5 (3 classes)
│   └── sample_dataset.csv         20 synthetic rows so the pipeline runs
├── backend/
│   ├── server.py                  FastAPI: ingest / sessions / query / collect / auth
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                      React + Vite + TS + Tailwind + Recharts
│   ├── src/
│   │   ├── App.tsx, main.tsx, api.ts, index.css
│   │   └── components/
│   │       ├── AuthPage.tsx       Login + signup screen
│   │       ├── LiveCounter.tsx    Live exercise + reps + form bar
│   │       ├── SessionHistory.tsx Past sessions, recharts bar chart
│   │       └── FloatingChat.tsx   Streaming Ollama chat widget
│   ├── nginx.conf, Dockerfile, package.json, tailwind.config.js, ...
├── tools/
│   └── simulate_device.py         Fake ESP32 — streams synthetic windows
├── docker-compose.yml
├── Makefile
├── .env.example
└── README.md
```

## Quick start (Docker — one command)

```bash
cp .env.example .env          # then edit secrets if you want
docker compose up --build     # or: make up
```

Then open:
- Frontend: http://localhost:5173 → **Sign up** or log in (`admin` / `admin`)
- Backend:  http://localhost:8000/api/health

The frontend talks to the backend through an nginx reverse-proxy on the same
origin, so there are no CORS surprises and the browser doesn't need to know
the backend URL.

SQLite is persisted in a named volume (`gym-data`). The host directory
`backend/` is mounted read-only at `/models` inside the container — drop a
trained `model.h5` in there and `docker compose restart backend` to switch
from heuristic mode to the trained CNN.

## Local NLP via Ollama (recommended)

The floating chat in the bottom-right corner streams answers from a local
LLM by default. **No API keys, no outbound traffic.**

### Install + start Ollama

```bash
brew install --cask ollama          # macOS

# Ollama must bind to all interfaces so the Docker container can reach it
brew services stop ollama 2>/dev/null
OLLAMA_HOST=0.0.0.0:11434 ollama serve &

# Pull a small, fast model (≈1.3 GB; runs well on Apple Silicon)
ollama pull llama3.2:1b
```

Want better answers? Bigger models, same flow:
```bash
ollama pull llama3.2:3b           # ~2 GB, noticeably smarter
ollama pull qwen2.5:3b            # strong on structured Q&A
```
Then set `OLLAMA_MODEL=llama3.2:3b` in `.env` and `docker compose restart backend`.

### Verify Ollama is reachable from the container

```bash
docker compose exec backend python -c "import urllib.request as r;print(r.urlopen('http://host.docker.internal:11434/api/tags').read().decode())"
# should print the models JSON, including llama3.2:1b
```

### NLP fallback chain

`/api/query` and `/api/query/stream` try in order:

1. **Ollama** if `OLLAMA_URL` is set (default).
2. **Anthropic Claude** if `ANTHROPIC_API_KEY` is set.
3. **Rule-based template** (always available; deterministic).

Set `OLLAMA_URL=` (empty) in `.env` to disable Ollama; the chain falls through
to whichever is configured next.

## Testing without hardware

Three options, easiest to most realistic:

### A. Frontend mock mode (zero setup)

In the auth screen → expand **Connection settings** → click **Switch to mock**.
The dashboard fakes live data, history, and chat replies.

### B. Simulator → real backend (recommended)

```bash
python tools/simulate_device.py --loop --speed 0.5
# options:
python tools/simulate_device.py --attach   # uses the session you started in the UI
python tools/simulate_device.py --routine quick
```

Synthetic curl/squat/rest waveforms shaped to match the heuristic — works
without a trained model.

### C. Curl smoke test

```bash
TOKEN=$(curl -sX POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/health
```

## Hardware setup (the real thing)

### 1. Find your Mac's LAN IP

```bash
ipconfig getifaddr en0
# e.g. 192.168.1.14
```

The ESP32 must be on the same WiFi (2.4 GHz; ESP32 classic doesn't do 5 GHz).

### 2. Edit `firmware/main/main.ino`

```cpp
#define WIFI_SSID   "Your WiFi name"
#define WIFI_PASS   "Your WiFi password"
#define SERVER_URL  "http://192.168.1.14:8000/api/ingest"
#define DEVICE_ID   "rudy-wrist-01"
```

> ℹ `/api/ingest` does **not** require auth (devices on the LAN just POST).
> A per-`device_id` rate limit (100/min) protects from abuse.

### 3. Upload via Arduino IDE

- Add ESP32 board support (Settings → Additional boards manager URLs):
  `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
- **Board:** ESP32 Dev Module (or your specific variant).
- **Port:** `/dev/cu.usbserial-*` or `/dev/cu.usbmodem*` after plugging in.
- Hit **Upload** (hold BOOT button if it stalls at *Connecting…*).

Open Serial Monitor at **115200 baud** — you should see:

```
WiFi OK, IP=192.168.1.57
Ready.
POST 200
POST 200
```

Each `POST 200` is one 1-second window successfully ingested.

## Training your own model

The whole pipeline (collect → train → reload) is doable from one terminal,
no firmware reflash needed.

### 1. Get a JWT (24h)

```fish
set -x TOKEN (curl -sX POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
```

### 2. Collect (~90s per label)

```fish
docker compose exec backend rm -f /data/collected_dataset.csv

for LABEL in curl squat rest
    echo ">> $LABEL — do it for ~90 seconds, then press Enter"
    curl -sX POST http://localhost:8000/api/collect/start \
        -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d "{\"label\":\"$LABEL\"}"
    read
    curl -sX POST http://localhost:8000/api/collect/stop -H "Authorization: Bearer $TOKEN"
end

docker compose cp backend:/data/collected_dataset.csv ml/collected_dataset.csv
```

Sanity-check the per-label counts (target ≥120 per class):

```bash
python3 -c "
import csv, collections
c = collections.Counter()
for row in csv.DictReader(open('ml/collected_dataset.csv')):
    c[row['label']] += 1
print(c)
"
```

**Collection tips that matter** (these fix more than any model tweak):
- **rest**: ESP32 on a table, hand off it. Any fidget → looks like exercise.
- **curl**: deliberate, ~2s/rep, elbow pinned. Don't rush.
- **squat**: arms locked at sides — don't let them swing or it looks like a curl.

### 3. Train + reload

```bash
docker run --rm -v "$PWD/ml:/ml" -v "$PWD/backend:/backend" -w /ml \
    python:3.11-slim bash -lc \
    "pip install --quiet pandas numpy tensorflow && python train_model.py --csv /ml/collected_dataset.csv --out /backend/model.h5"

docker compose restart backend
```

Backend logs should now say `[model] Loaded Keras model from /models/model.h5`.

## Make targets

| Command          | What it does                                         |
|------------------|------------------------------------------------------|
| `make up`        | Build + run backend + frontend (detached)            |
| `make down`      | Stop containers                                      |
| `make logs`      | Tail logs from both services                         |
| `make rebuild`   | Full rebuild + recreate                              |
| `make train`     | Train a model in a throwaway container               |
| `make clean`     | Stop + delete volumes + delete `sessions.db`         |

## API reference

JWT-protected routes need `Authorization: Bearer <jwt>`. The device endpoint
`/api/ingest` is public on the LAN (rate-limited).

| Method | Path                       | Auth | Purpose                                                |
|--------|----------------------------|------|--------------------------------------------------------|
| POST   | `/api/auth/signup`         | —    | Create a user (3-32 char username, ≥6 char password)   |
| POST   | `/api/auth/login`          | —    | Username/password → JWT                                |
| GET    | `/api/auth/me`             | ✓    | Current user                                           |
| POST   | `/api/ingest`              | —    | Classify a 50-sample window; bumps rep counter         |
| POST   | `/api/session/start`       | ✓    | Start a session                                        |
| POST   | `/api/session/end`         | ✓    | End the active session                                 |
| GET    | `/api/session/{id}`        | ✓    | Session summary with per-set reps + form score         |
| GET    | `/api/sessions`            | ✓    | Recent sessions for the authed user                    |
| GET    | `/api/live`                | ✓    | Latest prediction + rep/form snapshot (poll every 500ms)|
| POST   | `/api/query`               | ✓    | NLP coach query (non-streaming)                        |
| POST   | `/api/query/stream`        | ✓    | Streaming NLP — ndjson token chunks                    |
| POST   | `/api/collect/start`       | ✓    | Begin tagging incoming windows with a label            |
| POST   | `/api/collect/stop`        | ✓    | Stop tagging                                           |
| GET    | `/api/collect/status`      | ✓    | Current label + per-label counts                       |
| GET    | `/api/health`              | —    | Liveness probe                                         |

### Form score

For each set, the backend counts how many windows were classified with
`confidence > 0.85`. Form score = `hi_conf / total_windows`, rendered in the
UI as a green / yellow / red bar (≥0.85 / ≥0.6 / below).

### Streaming chat protocol

`/api/query/stream` returns `application/x-ndjson`. Each line is one event:

```
{"type":"start","provider":"ollama:llama3.2:1b"}
{"type":"token","content":"You "}
{"type":"token","content":"crushed "}
{"type":"token","content":"that workout!"}
{"type":"done"}
```

Errors arrive as `{"type":"error","message":"..."}` followed by a fallback
token + `done`.

## HTTPS (optional)

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 \
    -subj "/CN=localhost"

# Skip docker-compose; run uvicorn directly with TLS:
uvicorn server:app --host 0.0.0.0 --port 8443 \
    --ssl-keyfile key.pem --ssl-certfile cert.pem
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[model] … HEURISTIC mode` in backend logs | Expected before training; train + restart |
| `ESP32 WiFi FAILED` | Wrong SSID/PW, 5GHz-only network, distance to AP |
| Chat answers `{"now": 1776...}` style garbage | Tiny LLM regurgitating context; pull a bigger model (`llama3.2:3b`) and restart backend |
| `ollama unreachable` / `404` | Ollama bound to 127.0.0.1 only — restart with `OLLAMA_HOST=0.0.0.0:11434` |
| `model 'llama3.2:1b' not found` | `ollama pull llama3.2:1b` (or whatever `OLLAMA_MODEL` is set to) |
| `HTTP 401` from auth'd routes | Token expired (24h) — log in again; for fish: `set -x TOKEN ...` |
| `HTTP 429` from `/api/ingest` | Rate limit (100/min/device); raise `RATE_LIMIT_PER_MIN` in `server.py` |
| Reps not counting | Either model is misclassifying (collect more data) or you forgot **Start session** in the UI |
| Frontend stuck on "checking…" | API base wrong or backend down → app auto-falls back to mock mode |
| `Connection refused` from ESP32 to Mac | macOS firewall blocking Docker; allow `com.docker.backend` in System Settings |
