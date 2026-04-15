# Smart Gym Rep Counter & Form Checker

End-to-end system that watches an ESP32 + ADXL345 wrist sensor, classifies
bicep curls / squats / pushups / rest with a 1-D CNN, counts reps, scores form
per-set, stores sessions in SQLite, and exposes a React dashboard with an NLP
query box powered by Claude.

## Architecture

```
 ┌──────────────┐     HTTP JSON      ┌────────────────────┐     SQLite
 │ ESP32+ADXL345│ ──────────────────▶│ FastAPI backend    │──────────────▶  sessions.db
 │  firmware    │   /api/ingest      │ - JWT auth         │
 └──────────────┘   50Hz / windows   │ - rep counter      │
                                     │ - Keras model      │
                                     │ - /api/query→Claude│
                                     └────────┬───────────┘
                                              │ REST (polled every 500ms)
                                              ▼
                                     ┌────────────────────┐
                                     │ React + Tailwind   │
                                     │  LiveCounter       │
                                     │  SessionHistory    │
                                     │  QueryBox          │
                                     └────────────────────┘
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
      │              │   SDO→GND: addr 0x53│             │
      └──────────────┘                    └─────────────┘
                                      (leave CS pulled HIGH for I²C)
```

Mount the board to the wrist with the Z-axis pointing away from the skin; the
default heuristic (used when no Keras model is loaded) assumes that mounting.

## Repo layout

```
.
├── firmware/main.ino              ESP32 Arduino sketch (50Hz, sliding window)
├── ml/
│   ├── collect_data.py            Label + save windows to CSV
│   ├── train_model.py             1-D CNN in Keras → model.h5
│   └── sample_dataset.csv         20 synthetic rows so the pipeline runs
├── backend/
│   ├── server.py                  FastAPI: ingest / sessions / query / auth
│   └── requirements.txt
├── frontend/                      React + Vite + TS + Tailwind + Recharts
│   ├── index.html
│   ├── package.json
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── src/
│       ├── main.tsx, App.tsx, api.ts, index.css
│       └── components/LiveCounter.tsx, SessionHistory.tsx, QueryBox.tsx
└── README.md
```

## Quick start (Docker — everything in one command)

```bash
cp .env.example .env        # then edit secrets as needed
docker compose up --build   # or: make up
```

Then open:

- Frontend: http://localhost:5173
- Backend:  http://localhost:8000/api/health

The frontend talks to the backend through an nginx reverse-proxy on the same
origin, so there are no CORS surprises and the browser doesn't need to know
the backend URL.

SQLite is persisted in a named volume (`gym-data`). Your host directory
`backend/` is mounted read-only at `/models` inside the container — drop a
`model.h5` in there and restart the backend container to switch from heuristic
mode to the trained CNN:

```bash
# after training creates backend/model.h5
docker compose restart backend
```

Useful Make targets:

| Command          | What it does                                         |
|------------------|------------------------------------------------------|
| `make up`        | Build + run backend + frontend (detached)            |
| `make down`      | Stop containers                                      |
| `make logs`      | Tail logs from both services                         |
| `make rebuild`   | Full rebuild + recreate                              |
| `make train`     | Train a model in a throwaway container (writes `backend/model.h5`) |
| `LABEL=curl make collect` | Start the data-collection server for a label (port 8000) |
| `make clean`     | Stop + delete volumes + delete `sessions.db`         |

## Manual (non-Docker) setup

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Required for production; any string works for local dev.
export JWT_SECRET="change-me"
# Demo creds used by /api/auth/login
export DEMO_USER="admin" DEMO_PASS="admin"
# Optional — enables Claude for /api/query
export ANTHROPIC_API_KEY="sk-ant-..."

uvicorn server:app --host 0.0.0.0 --port 8000
```

The backend runs with a **heuristic classifier** if `model.h5` is not present,
so the whole pipeline is usable before you have training data.

#### HTTPS

Add `--ssl-keyfile` and `--ssl-certfile`:

```bash
uvicorn server:app --host 0.0.0.0 --port 8443 \
    --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Generate a self-signed cert for dev:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 \
    -subj "/CN=localhost"
```

### 2. Get a JWT

```bash
curl -X POST http://localhost:8000/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin"}'
# → { "token": "...", "expires_in": 86400, "token_type": "bearer" }
```

Put this token in the ESP32 firmware (`#define AUTH_TOKEN ...`).

### 3. Collect training data

Run the collector with the label you want, flash the ESP32, do the exercise
steadily for ~30–60 seconds, then stop. Repeat for each class.

```bash
cd ml
pip install fastapi uvicorn pandas numpy tensorflow

python collect_data.py --label curl   --out sample_dataset.csv   # do curls
python collect_data.py --label squat  --out sample_dataset.csv   # do squats
python collect_data.py --label pushup --out sample_dataset.csv   # do pushups
python collect_data.py --label rest   --out sample_dataset.csv   # stay still
```

A running counter prints to the terminal each time a labelled window lands.

### 4. Train

```bash
cd ml
python train_model.py --csv sample_dataset.csv --out ../backend/model.h5
```

This prints accuracy + confusion matrix and writes `backend/model.h5`. Restart
the backend to pick up the new model — it will switch out of heuristic mode.

### 5. Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

Open the header "Login" panel, enter the API base + demo credentials, and hit
**Log in**. The dashboard polls `/api/live` every 500 ms.

> The frontend auto-falls-back to **mock mode** when the backend is offline, so
> you can develop UI without the backend. Toggle via the "Switch to mock / live"
> button in the header.

### 6. Firmware

1. Open `firmware/main.ino` in the Arduino IDE (board: "ESP32 Dev Module").
2. Fill in `WIFI_SSID`, `WIFI_PASS`, `SERVER_URL`, `AUTH_TOKEN`, `DEVICE_ID`.
3. Upload. The serial monitor prints `POST 200` whenever a window is accepted.

## API reference

All endpoints except `/api/auth/login` and `/api/health` require
`Authorization: Bearer <jwt>`.

| Method | Path                         | Purpose                                          |
|--------|------------------------------|--------------------------------------------------|
| POST   | `/api/auth/login`            | Exchange username/password for a 24h JWT         |
| POST   | `/api/ingest`                | Classify a 50-sample window, update rep counter  |
| POST   | `/api/session/start`         | Start a new session (returns id)                 |
| POST   | `/api/session/end`           | Close the active session                         |
| GET    | `/api/session/{id}`          | Session summary with per-set reps & form score   |
| GET    | `/api/sessions`              | List recent sessions for the authed user         |
| GET    | `/api/live`                  | Latest prediction + rep/form snapshot (poll 500ms)|
| POST   | `/api/query`                 | NLP coach query, forwarded to Claude Haiku 4.5   |
| GET    | `/api/health`                | Liveness probe                                   |

### Rate limiting

`/api/ingest` enforces **100 requests per minute per `device_id`**. Excess
requests return HTTP 429.

### Form score

For each set, the backend counts how many windows were classified with
`confidence > 0.85`. Form score = `hi_conf / total_windows` rendered in the UI
as a green/yellow/red bar (≥0.85 / ≥0.6 / below).

## Troubleshooting

- **`model.h5 not found` log line**: expected before training; server runs in
  heuristic mode. Train and restart.
- **ESP32 `WiFi FAILED`**: confirm SSID/PW, router 2.4 GHz band, distance.
- **`HTTP 401`**: JWT missing, malformed, or expired (24 h). Log in again.
- **`HTTP 429`**: firmware is posting faster than 100/min; raise
  `WINDOW_STRIDE` or the server-side limit.
- **Frontend stuck on "checking…"**: CORS disabled or API base is wrong. The
  app falls back to mock mode automatically after the first health check.
