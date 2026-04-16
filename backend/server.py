"""
server.py — Smart Gym Rep Counter backend.

Responsibilities
----------------
- Load a trained Keras model at startup (if available).
- Classify incoming 1-second accelerometer windows.
- Count reps by watching for active -> rest -> active transitions.
- Persist sessions and per-set form scores to SQLite.
- Expose a small NLP query endpoint that forwards session data to Claude.
- JWT auth (HS256, 24h) on every API route; per-device rate limit on /api/ingest.

Run
---
    pip install -r requirements.txt
    export JWT_SECRET=change-me                     # required in prod
    export ANTHROPIC_API_KEY=sk-ant-...             # for /api/query
    export DEMO_USER=admin DEMO_PASS=admin          # /api/auth/login credentials
    uvicorn server:app --host 0.0.0.0 --port 8000

For HTTPS add:
    uvicorn server:app --host 0.0.0.0 --port 8000 \
        --ssl-keyfile key.pem --ssl-certfile cert.pem
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import jwt
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LABELS = ["curl", "squat", "rest", "other"]
IDX_REST = LABELS.index("rest")
WINDOW = 50
CONFIDENCE_FORM_THRESHOLD = 0.85
RATE_LIMIT_PER_MIN = 120   # raised: firmware now sends every 0.5 s (2×/s)

# Rep counting tuning
# VALLEY_CONF  — if the model still predicts the exercise but with confidence
#                below this, we treat it as the "valley" between reps (arm at
#                the bottom / top of the movement).  Combined with the explicit
#                rest/other class this lets the counter work even when the brief
#                pause between reps is < 1 window (500 ms).
# ACTIVE_CONF  — confidence must rise back above this to credit a new rep.
# MIN_REP_S    — minimum seconds between two counted reps (~75 reps/min max).
VALLEY_CONF = 0.60
ACTIVE_CONF = 0.70
MIN_REP_S   = 0.8

BACKEND_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.environ.get("MODEL_PATH", BACKEND_DIR / "model.h5"))
DB_PATH = Path(os.environ.get("DB_PATH", BACKEND_DIR / "sessions.db"))
# Default collection output lives under /data (the named volume) so it survives
# container restarts and is host-visible via `docker compose cp`.
COLLECT_CSV = Path(os.environ.get("COLLECT_CSV", "/data/collected_dataset.csv"))

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
JWT_TTL_SECONDS = 24 * 3600

DEMO_USER = os.environ.get("DEMO_USER", "admin")
DEMO_PASS = os.environ.get("DEMO_PASS", "admin")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# Local NLP via Ollama (https://ollama.com). When the backend runs in Docker,
# `http://host.docker.internal:11434` reaches the Mac host's Ollama daemon.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_TIMEOUT_S = float(os.environ.get("OLLAMA_TIMEOUT_S", "30"))

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def db_init() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              username      TEXT PRIMARY KEY,
              password_hash TEXT NOT NULL,
              created_at    REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              user       TEXT    NOT NULL,
              started_at REAL    NOT NULL,
              ended_at   REAL
            );
            CREATE TABLE IF NOT EXISTS sets (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              exercise   TEXT    NOT NULL,
              reps       INTEGER NOT NULL DEFAULT 0,
              form_score REAL    NOT NULL DEFAULT 0.0,
              hi_conf    INTEGER NOT NULL DEFAULT 0,
              total      INTEGER NOT NULL DEFAULT 0,
              started_at REAL    NOT NULL,
              ended_at   REAL
            );
            CREATE INDEX IF NOT EXISTS idx_sets_session ON sets(session_id);
            """
        )
        conn.commit()

    # Seed the demo user from env so the old creds keep working out-of-the-box.
    with db_connect() as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE username = ?", (DEMO_USER,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                (DEMO_USER, hash_password(DEMO_PASS), time.time()),
            )
            conn.commit()
            print(f"[db] Seeded demo user '{DEMO_USER}'")


# --------- Password hashing (PBKDF2-HMAC-SHA256, stdlib only) ---------

_PBKDF2_ITERS = 200_000


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${digest.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        scheme, iters_s, salt_hex, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters)
    return hmac.compare_digest(digest, expected)


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def valid_username(u: str) -> bool:
    return bool(USERNAME_RE.match(u))


def valid_password(p: str) -> bool:
    return 6 <= len(p) <= 128


# ---------------------------------------------------------------------------
# Model wrapper (with a graceful mock when TF model is missing)
# ---------------------------------------------------------------------------


class Classifier:
    """Wraps a Keras model, falling back to a simple heuristic if absent."""

    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.model = None
        self.mock = True
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            print(f"[model] {self.model_path} not found — running in HEURISTIC mode")
            return
        try:
            import tensorflow as tf  # noqa: F401
            from tensorflow.keras.models import load_model
            self.model = load_model(self.model_path)
            self.mock = False
            print(f"[model] Loaded Keras model from {self.model_path}")
        except Exception as exc:  # pragma: no cover
            print(f"[model] Failed to load {self.model_path}: {exc}. Using heuristic.")
            self.model = None
            self.mock = True

    @staticmethod
    def _normalise(window: np.ndarray) -> np.ndarray:
        """Per-window, per-channel z-score — mirrors the training-time normalise()."""
        mu = window.mean(axis=0, keepdims=True)      # (1, 3)
        sd = window.std(axis=0, keepdims=True) + 1e-8
        return (window - mu) / sd

    def predict(self, window: np.ndarray) -> Tuple[int, float, List[float]]:
        """window shape: (50, 3).  Returns (class_idx, confidence, all_probs)."""
        if self.model is not None:
            norm_window = self._normalise(window)
            probs = self.model.predict(norm_window[np.newaxis, ...], verbose=0)[0]
            probs = np.asarray(probs, dtype=np.float32)
            return int(np.argmax(probs)), float(np.max(probs)), probs.tolist()
        # ---------- Heuristic fallback (4 classes: curl / squat / rest / other) ----------
        ax, ay, az = window[:, 0], window[:, 1], window[:, 2]
        energy = float(np.std(ax) + np.std(ay) + np.std(az))
        if energy < 0.08:
            probs = [0.02, 0.02, 0.94, 0.02]         # rest
        else:
            std_x, std_y, std_z = float(np.std(ax)), float(np.std(ay)), float(np.std(az))
            dom = int(np.argmax([std_x, std_y, std_z]))
            if dom == 2:
                probs = [0.08, 0.76, 0.08, 0.08]     # squat (z dominant)
            else:
                probs = [0.76, 0.08, 0.08, 0.08]     # curl (x or y dominant)
        arr = np.asarray(probs, dtype=np.float32)
        return int(np.argmax(arr)), float(np.max(arr)), arr.tolist()


# ---------------------------------------------------------------------------
# Rep counter
# ---------------------------------------------------------------------------


class RepCounter:
    """
    Thread-safe rep counter / set tracker.

    A rep is counted whenever we observe the sequence:
        active (X) -> rest -> active (X)
    where X is one of curl/squat. Each time the active exercise changes
    (e.g. curl -> squat) we open a new `set` row, so form score is per-set.
    """

    # How many consecutive windows of a DIFFERENT exercise we must see before
    # switching the active set. Prevents a single noisy "squat" blip mid-curl
    # from resetting the rep count.
    SWITCH_THRESHOLD = 3

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.session_id: Optional[int] = None
        self.current_exercise: Optional[str] = None
        self.last_active_exercise: Optional[str] = None
        # Smoothing: track consecutive predictions of a candidate new exercise
        self._pending_exercise: Optional[str] = None
        self._pending_count: int = 0
        self.state: str = "rest"            # "rest" or "active"
        self.reps: int = 0                  # reps for the current set
        self.hi_conf: int = 0
        self.total: int = 0
        self.set_id: Optional[int] = None
        # Valley detection: True while we are in the "bottom" of a rep
        # (either the model classified rest/other, or confidence dipped below
        # VALLEY_CONF while still predicting the exercise).
        self.in_valley: bool = False
        self.last_rep_time: float = 0.0     # wall-clock time of last counted rep
        self.last_prediction: Dict[str, Any] = {
            "exercise": "rest",
            "confidence": 0.0,
            "reps": 0,
            "form_score": 0.0,
            "session_id": None,
            "updated_at": time.time(),
        }

    # ---- session management ----

    def start_session(self, user: str) -> int:
        with self._lock, db_connect() as conn:
            cur = conn.execute(
                "INSERT INTO sessions(user, started_at) VALUES (?, ?)",
                (user, time.time()),
            )
            conn.commit()
            self.session_id = int(cur.lastrowid)
            self._reset_set_state()
            self.last_prediction.update(
                {"session_id": self.session_id, "reps": 0, "form_score": 0.0,
                 "exercise": "rest", "confidence": 0.0, "updated_at": time.time()}
            )
            return self.session_id

    def end_session(self) -> Optional[int]:
        with self._lock, db_connect() as conn:
            sid = self.session_id
            if sid is None:
                return None
            self._close_set_locked(conn)
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time(), sid),
            )
            conn.commit()
            self.session_id = None
            self._reset_set_state()
            return sid

    def _reset_set_state(self) -> None:
        self.current_exercise = None
        self.last_active_exercise = None
        self.state = "rest"
        self.reps = 0
        self.hi_conf = 0
        self.total = 0
        self.set_id = None
        self.in_valley = False
        self.last_rep_time = 0.0
        self._pending_exercise = None
        self._pending_count = 0

    def _open_set_locked(self, conn: sqlite3.Connection, exercise: str) -> None:
        cur = conn.execute(
            "INSERT INTO sets(session_id, exercise, started_at) VALUES (?, ?, ?)",
            (self.session_id, exercise, time.time()),
        )
        conn.commit()
        self.set_id = int(cur.lastrowid)
        self.current_exercise = exercise
        self.reps = 0
        self.hi_conf = 0
        self.total = 0

    def _close_set_locked(self, conn: sqlite3.Connection) -> None:
        if self.set_id is None:
            return
        form = (self.hi_conf / self.total) if self.total else 0.0
        conn.execute(
            "UPDATE sets SET reps=?, form_score=?, hi_conf=?, total=?, ended_at=? WHERE id=?",
            (self.reps, form, self.hi_conf, self.total, time.time(), self.set_id),
        )
        conn.commit()
        self.set_id = None

    # ---- per-window update ----

    def update(self, exercise: str, confidence: float) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            if self.session_id is not None:
                with db_connect() as conn:
                    # Switch set if the active exercise changed — but only
                    # after SWITCH_THRESHOLD consecutive windows of the new
                    # exercise. Prevents a single noisy "squat" blip mid-curl
                    # from resetting the rep count.
                    if exercise not in ("rest", "other"):
                        if self.current_exercise is None:
                            # First active prediction — open set immediately
                            self._open_set_locked(conn, exercise)
                            self._pending_exercise = None
                            self._pending_count = 0
                        elif self.current_exercise != exercise:
                            # Different exercise — accumulate toward switch
                            if self._pending_exercise == exercise:
                                self._pending_count += 1
                            else:
                                self._pending_exercise = exercise
                                self._pending_count = 1
                            # Only switch after N consecutive windows agree
                            if self._pending_count >= self.SWITCH_THRESHOLD:
                                self._close_set_locked(conn)
                                self._open_set_locked(conn, exercise)
                                self._pending_exercise = None
                                self._pending_count = 0
                        else:
                            # Same exercise as current — reset any pending switch
                            self._pending_exercise = None
                            self._pending_count = 0

                    # Track form score counters for the current set
                    if self.set_id is not None:
                        self.total += 1
                        if confidence >= CONFIDENCE_FORM_THRESHOLD:
                            self.hi_conf += 1

                    # ── Rep state machine ──────────────────────────────────
                    # A rep is the sequence:  peak → valley → peak (same ex.)
                    #
                    # "valley" is entered when:
                    #   a) the model predicts rest/other  (arm fully at rest), OR
                    #   b) the model still predicts the exercise but with low
                    #      confidence (< VALLEY_CONF) — catches the brief pause
                    #      at the bottom/top of a fast rep that is shorter than
                    #      one 1-second window.
                    #
                    # A rep is counted when confidence rises back above
                    # ACTIVE_CONF for the same exercise, provided the refractory
                    # period (MIN_REP_S) has elapsed since the last counted rep.
                    # This prevents double-counting on overlapping windows.
                    if exercise in ("rest", "other"):
                        # Explicit rest / unrecognised motion → enter valley
                        if self.state == "active":
                            self.state = "rest"
                        self.in_valley = True
                    elif confidence < VALLEY_CONF:
                        # Confidence dip while still predicting the exercise
                        # (transition point between reps)
                        if self.state == "active":
                            self.state = "rest"
                        self.in_valley = True
                    else:
                        # Confidence is high — we are in the active part of a rep
                        if (
                            self.in_valley
                            and self.last_active_exercise == exercise
                            and (now - self.last_rep_time) >= MIN_REP_S
                        ):
                            self.reps += 1
                            self.last_rep_time = now
                        self.in_valley = False
                        self.state = "active"
                        self.last_active_exercise = exercise

                    # Persist running counters for this set
                    if self.set_id is not None:
                        form = (self.hi_conf / self.total) if self.total else 0.0
                        conn.execute(
                            "UPDATE sets SET reps=?, form_score=?, hi_conf=?, total=? WHERE id=?",
                            (self.reps, form, self.hi_conf, self.total, self.set_id),
                        )
                        conn.commit()

            form_score = (self.hi_conf / self.total) if self.total else 0.0
            # When the model predicts "other" (unknown motion), keep showing the
            # previously-known exercise in the live snapshot. Reps / form still
            # update, but the UI doesn't flicker to "other".
            if exercise == "other" and self.last_prediction.get("exercise") not in (None, "other"):
                display_exercise = self.last_prediction["exercise"]
                display_confidence = self.last_prediction["confidence"]
            else:
                display_exercise = exercise
                display_confidence = confidence
            self.last_prediction = {
                "exercise": display_exercise,
                "confidence": display_confidence,
                "reps": self.reps,
                "form_score": form_score,
                "session_id": self.session_id,
                "updated_at": now,
            }
            # The response to /api/ingest still reports the raw prediction
            # so the ESP32 Serial Monitor shows what the model actually saw.
            return {
                "exercise": exercise,
                "confidence": confidence,
                "reps": self.reps,
                "form_score": form_score,
                "session_id": self.session_id,
                "updated_at": now,
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.last_prediction)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class Collector:
    """Appends labelled windows to a CSV. Toggle on/off at runtime."""

    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path
        self._lock = threading.Lock()
        self._label: Optional[str] = None
        self._counts: Dict[str, int] = {}

    def _ensure_header(self) -> None:
        if self.csv_path.exists():
            return
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        import csv as _csv
        with self.csv_path.open("w", newline="") as f:
            w = _csv.writer(f)
            cols: List[str] = []
            for prefix in ("ax", "ay", "az"):
                cols.extend(f"{prefix}{i}" for i in range(WINDOW))
            cols.append("label")
            w.writerow(cols)

    def start(self, label: str) -> Dict[str, Any]:
        if label not in LABELS:
            raise HTTPException(
                status_code=400,
                detail=f"label must be one of {LABELS}",
            )
        with self._lock:
            self._ensure_header()
            self._label = label
            self._counts.setdefault(label, 0)
        return {"status": "collecting", "label": label, "count": self._counts[label]}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            was = self._label
            self._label = None
        return {"status": "stopped", "last_label": was, "counts": dict(self._counts)}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "collecting": self._label is not None,
                "label": self._label,
                "counts": dict(self._counts),
                "csv_path": str(self.csv_path),
                "total": sum(self._counts.values()),
            }

    def maybe_append(self, ax: List[float], ay: List[float], az: List[float]) -> None:
        with self._lock:
            if not self._label:
                return
            label = self._label
        # Release the lock before filesystem I/O; use a short follow-up lock
        # only for bumping the counter.
        import csv as _csv
        with self.csv_path.open("a", newline="") as f:
            w = _csv.writer(f)
            w.writerow([f"{v:.4f}" for v in (ax + ay + az)] + [label])
        with self._lock:
            self._counts[label] = self._counts.get(label, 0) + 1


class PerDeviceRateLimiter:
    """Fixed window counter keyed by device id (max N per 60s)."""

    def __init__(self, max_per_min: int) -> None:
        self.max = max_per_min
        self._lock = threading.Lock()
        self._buckets: Dict[str, Deque[float]] = {}

    def check(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - 60.0
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max:
                return False
            bucket.append(now)
            return True


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def make_token(user: str) -> str:
    now = int(time.time())
    payload = {"sub": user, "iat": now, "exp": now + JWT_TTL_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def require_auth(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    parts = authorization.split(None, 1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = decoded.get("sub")
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return str(user)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    token_type: str = "bearer"
    username: str


class IngestRequest(BaseModel):
    device_id: str = Field(default="unknown")
    timestamp: Optional[int] = None
    ax: List[float]
    ay: List[float]
    az: List[float]


class IngestResponse(BaseModel):
    exercise: str
    confidence: float
    probabilities: Dict[str, float]
    reps: int
    form_score: float
    session_id: Optional[int]


class StartSessionResponse(BaseModel):
    session_id: int


class EndSessionResponse(BaseModel):
    session_id: int


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Smart Gym Rep Counter", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

classifier: Classifier
counter: RepCounter
rate_limiter: PerDeviceRateLimiter
collector: Collector


@app.on_event("startup")
def _startup() -> None:
    global classifier, counter, rate_limiter, collector
    db_init()
    classifier = Classifier(MODEL_PATH)
    counter = RepCounter()
    rate_limiter = PerDeviceRateLimiter(RATE_LIMIT_PER_MIN)
    collector = Collector(COLLECT_CSV)
    print("[startup] ready")


@app.exception_handler(HTTPException)
def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status": exc.status_code},
    )


@app.exception_handler(Exception)
def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    return JSONResponse(
        status_code=500,
        content={"error": f"internal error: {exc}", "status": 500},
    )


# ---- Auth ----


@app.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT username, password_hash FROM users WHERE username = ?",
            (body.username,),
        ).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return LoginResponse(
        token=make_token(row["username"]),
        expires_in=JWT_TTL_SECONDS,
        username=row["username"],
    )


@app.post("/api/auth/signup", response_model=LoginResponse)
def signup(body: SignupRequest) -> LoginResponse:
    if not valid_username(body.username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-32 chars: letters, digits, _ . -",
        )
    if not valid_password(body.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be 6-128 characters",
        )
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (body.username,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")
        conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
            (body.username, hash_password(body.password), time.time()),
        )
        conn.commit()
    return LoginResponse(
        token=make_token(body.username),
        expires_in=JWT_TTL_SECONDS,
        username=body.username,
    )


@app.get("/api/auth/me")
def me(user: str = Depends(require_auth)) -> Dict[str, str]:
    return {"username": user}


# ---- Ingest ----


def _validate_window(r: IngestRequest) -> np.ndarray:
    if not (len(r.ax) == len(r.ay) == len(r.az) == WINDOW):
        raise HTTPException(
            status_code=400,
            detail=f"ax/ay/az must each contain {WINDOW} samples",
        )
    return np.stack(
        [np.asarray(r.ax, dtype=np.float32),
         np.asarray(r.ay, dtype=np.float32),
         np.asarray(r.az, dtype=np.float32)],
        axis=-1,
    )


@app.post("/api/ingest", response_model=IngestResponse)
def ingest(body: IngestRequest) -> IngestResponse:
    # No auth on /api/ingest — devices on the LAN just POST. Rate limit
    # (per device_id) still protects the endpoint from abuse.
    if not rate_limiter.check(body.device_id or "unknown"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    window = _validate_window(body)
    # If data-collection is active, persist the window with its label.
    collector.maybe_append(body.ax, body.ay, body.az)

    cls_idx, conf, probs = classifier.predict(window)
    exercise = LABELS[cls_idx]
    state = counter.update(exercise, conf)

    return IngestResponse(
        exercise=exercise,
        confidence=round(conf, 4),
        probabilities={LABELS[i]: round(float(probs[i]), 4) for i in range(min(len(LABELS), len(probs)))},
        reps=state["reps"],
        form_score=round(state["form_score"], 4),
        session_id=state["session_id"],
    )


# ---- Session lifecycle ----


@app.post("/api/session/start", response_model=StartSessionResponse)
def session_start(user: str = Depends(require_auth)) -> StartSessionResponse:
    sid = counter.start_session(user)
    return StartSessionResponse(session_id=sid)


@app.post("/api/session/end", response_model=EndSessionResponse)
def session_end(user: str = Depends(require_auth)) -> EndSessionResponse:
    sid = counter.end_session()
    if sid is None:
        raise HTTPException(status_code=400, detail="No active session")
    return EndSessionResponse(session_id=sid)


def _session_summary(session_id: int) -> Dict[str, Any]:
    with db_connect() as conn:
        sess = conn.execute(
            "SELECT id, user, started_at, ended_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not sess:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        set_rows = conn.execute(
            "SELECT id, exercise, reps, form_score, started_at, ended_at "
            "FROM sets WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    sets = [dict(r) for r in set_rows]
    reps_per_exercise: Dict[str, int] = {}
    form_sum: Dict[str, float] = {}
    form_cnt: Dict[str, int] = {}
    for s in sets:
        reps_per_exercise[s["exercise"]] = reps_per_exercise.get(s["exercise"], 0) + int(s["reps"])
        form_sum[s["exercise"]] = form_sum.get(s["exercise"], 0.0) + float(s["form_score"])
        form_cnt[s["exercise"]] = form_cnt.get(s["exercise"], 0) + 1
    form_per_exercise = {
        k: round(form_sum[k] / form_cnt[k], 4) if form_cnt[k] else 0.0
        for k in form_sum
    }
    return {
        "id": sess["id"],
        "user": sess["user"],
        "started_at": sess["started_at"],
        "ended_at": sess["ended_at"],
        "sets": sets,
        "reps_per_exercise": reps_per_exercise,
        "form_per_exercise": form_per_exercise,
        "total_reps": sum(reps_per_exercise.values()),
    }


@app.get("/api/session/{session_id}")
def session_get(session_id: int, user: str = Depends(require_auth)) -> Dict[str, Any]:
    return _session_summary(session_id)


@app.get("/api/sessions")
def sessions_list(user: str = Depends(require_auth)) -> Dict[str, Any]:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, user, started_at, ended_at FROM sessions "
            "WHERE user = ? ORDER BY id DESC LIMIT 50",
            (user,),
        ).fetchall()
    return {"sessions": [dict(r) for r in rows]}


# ---- Data collection ----


class CollectStartBody(BaseModel):
    label: str


@app.post("/api/collect/start")
def collect_start(body: CollectStartBody, user: str = Depends(require_auth)) -> Dict[str, Any]:
    return collector.start(body.label)


@app.post("/api/collect/stop")
def collect_stop(user: str = Depends(require_auth)) -> Dict[str, Any]:
    return collector.stop()


@app.get("/api/collect/status")
def collect_status(user: str = Depends(require_auth)) -> Dict[str, Any]:
    return collector.status()


# ---- Live polling ----


@app.get("/api/live")
def live(user: str = Depends(require_auth)) -> Dict[str, Any]:
    snap = counter.snapshot()
    return {
        **snap,
        "model_mode": "heuristic" if classifier.mock else "keras",
    }


# ---- NLP query ----


def _current_context(user: str) -> Dict[str, Any]:
    """Session-data blob scoped to a user."""
    with db_connect() as conn:
        sessions = conn.execute(
            "SELECT id, user, started_at, ended_at FROM sessions "
            "WHERE user = ? ORDER BY id DESC LIMIT 10",
            (user,),
        ).fetchall()
        sess_ids = [s["id"] for s in sessions] or [-1]
        placeholders = ",".join("?" * len(sess_ids))
        sets = conn.execute(
            f"SELECT session_id, exercise, reps, form_score, started_at, ended_at "
            f"FROM sets WHERE session_id IN ({placeholders}) "
            f"ORDER BY session_id DESC, id ASC",
            sess_ids,
        ).fetchall()
    return {
        "now": time.time(),
        "user": user,
        "live": counter.snapshot(),
        "sessions": [dict(s) for s in sessions],
        "sets": [dict(s) for s in sets],
    }


def _context_summary(ctx: Dict[str, Any]) -> str:
    """Render the context as a compact natural-language brief.

    Small LLMs (llama3.2:1b) choke on raw JSON and tend to echo fields.
    A prose summary of the same facts lands much better.
    """
    lines: List[str] = []
    user = ctx.get("user", "the user")
    lines.append(f"User: {user}")

    live = ctx.get("live") or {}
    if live.get("session_id"):
        lines.append(
            f"Live status: currently in session {live['session_id']}, "
            f"exercise={live.get('exercise')}, reps={live.get('reps', 0)}, "
            f"form={round(float(live.get('form_score', 0)) * 100)}%."
        )
    else:
        lines.append("Live status: no active session.")

    sessions = ctx.get("sessions") or []
    sets = ctx.get("sets") or []
    if not sessions:
        lines.append("History: no past workouts recorded yet.")
        return "\n".join(lines)

    # Today's date (UTC-ish, good enough for a coach summary).
    now = ctx.get("now") or time.time()
    day_start = now - 24 * 3600
    today_sets = [s for s in sets if (s.get("started_at") or 0) >= day_start]

    totals_all: Dict[str, int] = {}
    totals_today: Dict[str, int] = {}
    worst = ("none", 1.01)
    best = ("none", -0.01)
    for s in sets:
        ex = s["exercise"]
        reps = int(s["reps"])
        fs = float(s["form_score"])
        totals_all[ex] = totals_all.get(ex, 0) + reps
        if fs < worst[1]:
            worst = (ex, fs)
        if fs > best[1]:
            best = (ex, fs)
    for s in today_sets:
        totals_today[s["exercise"]] = totals_today.get(s["exercise"], 0) + int(s["reps"])

    lines.append(f"Recent sessions: {len(sessions)} (most recent id={sessions[0]['id']}).")
    if totals_today:
        tt = ", ".join(f"{k}:{v}" for k, v in totals_today.items())
        lines.append(f"Today's reps: {tt} (total={sum(totals_today.values())}).")
    else:
        lines.append("Today's reps: none yet.")
    if totals_all:
        ta = ", ".join(f"{k}:{v}" for k, v in totals_all.items())
        lines.append(f"All-time (last 10 sessions): {ta}.")
    if worst[0] != "none":
        lines.append(f"Worst-form exercise: {worst[0]} at {round(worst[1]*100)}%.")
    if best[0] != "none":
        lines.append(f"Best-form exercise: {best[0]} at {round(best[1]*100)}%.")
    # Include last 8 sets as a mini log
    if sets:
        log = []
        for s in sets[:8]:
            log.append(f"{s['exercise']}×{s['reps']} (form {round(float(s['form_score'])*100)}%)")
        lines.append("Recent sets: " + "; ".join(log) + ".")

    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You are a friendly, concise gym coach. Keep replies to 1-3 short "
    "sentences. Use ONLY the facts you are given about the user's workout. "
    "Never invent numbers. If the facts don't answer the question, say so "
    "briefly and suggest what to track. Greet casually when the user says hi."
)


def _build_user_prompt(question: str, summary: str) -> str:
    return (
        f"Here are the current facts about the user's workout:\n"
        f"---\n{summary}\n---\n\n"
        f"User says: {question}"
    )


def _ollama_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        f"{OLLAMA_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ollama {path} HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ollama unreachable at {OLLAMA_URL}{path}: {e}") from e


def _ask_ollama(question: str, summary: str) -> str:
    """Non-streaming Ollama call. Tries /api/chat, falls back to /api/generate."""
    user_msg = _build_user_prompt(question, summary)
    try:
        payload = _ollama_post("/api/chat", {
            "model": OLLAMA_MODEL,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 256},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        })
        text = ((payload.get("message") or {}).get("content") or "").strip()
        if text:
            return text
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise

    payload = _ollama_post("/api/generate", {
        "model": OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 256},
        "system": SYSTEM_PROMPT,
        "prompt": user_msg,
    })
    text = (payload.get("response") or "").strip()
    if not text:
        raise RuntimeError(f"ollama returned empty response: {payload}")
    return text


def _stream_ollama(question: str, summary: str):
    """Yield text chunks from Ollama. Tries /api/chat first, falls back to /api/generate."""
    import urllib.request
    import urllib.error

    user_msg = _build_user_prompt(question, summary)

    def open_stream(path: str, body: Dict[str, Any]):
        req = urllib.request.Request(
            f"{OLLAMA_URL}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S)

    # Try /api/chat
    try:
        resp = open_stream("/api/chat", {
            "model": OLLAMA_MODEL,
            "stream": True,
            "options": {"temperature": 0.3, "num_predict": 256},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        })
        saw_any = False
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            obj = json.loads(line)
            chunk = (obj.get("message") or {}).get("content") or ""
            if chunk:
                saw_any = True
                yield chunk
            if obj.get("done"):
                break
        if saw_any:
            return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise RuntimeError(f"ollama /api/chat HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ollama unreachable: {e}") from e

    # Fallback: /api/generate
    try:
        resp = open_stream("/api/generate", {
            "model": OLLAMA_MODEL,
            "stream": True,
            "options": {"temperature": 0.3, "num_predict": 256},
            "system": SYSTEM_PROMPT,
            "prompt": user_msg,
        })
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            obj = json.loads(line)
            chunk = obj.get("response") or ""
            if chunk:
                yield chunk
            if obj.get("done"):
                break
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"ollama /api/generate HTTP {e.code}: {body}") from e


def _ask_anthropic(question: str, summary: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(question, summary)}],
    )
    text = "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise RuntimeError("anthropic returned empty response")
    return text


def _stream_anthropic(question: str, summary: str):
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with client.messages.stream(
        model=ANTHROPIC_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(question, summary)}],
    ) as s:
        for text in s.text_stream:
            if text:
                yield text


@app.post("/api/query", response_model=QueryResponse)
def query(body: QueryRequest, user: str = Depends(require_auth)) -> QueryResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    ctx = _current_context(user)
    summary = _context_summary(ctx)

    errors: List[str] = []
    if OLLAMA_URL:
        try:
            return QueryResponse(answer=_ask_ollama(question, summary))
        except Exception as e:
            errors.append(str(e))
    if ANTHROPIC_API_KEY:
        try:
            return QueryResponse(answer=_ask_anthropic(question, summary))
        except Exception as e:
            errors.append(str(e))

    ans = _local_answer(question, ctx)
    if errors:
        ans = f"{ans}  [nlp fallback — errors: {' | '.join(errors)}]"
    return QueryResponse(answer=ans)


@app.post("/api/query/stream")
def query_stream(body: QueryRequest, user: str = Depends(require_auth)) -> StreamingResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    ctx = _current_context(user)
    summary = _context_summary(ctx)

    def event(kind: str, **extra: Any) -> bytes:
        return (json.dumps({"type": kind, **extra}) + "\n").encode("utf-8")

    def generate():
        provider = None
        errors: List[str] = []
        stream = None

        if OLLAMA_URL:
            try:
                stream = _stream_ollama(question, summary)
                provider = f"ollama:{OLLAMA_MODEL}"
            except Exception as e:
                errors.append(f"ollama: {e}")
                stream = None

        if stream is None and ANTHROPIC_API_KEY:
            try:
                stream = _stream_anthropic(question, summary)
                provider = f"anthropic:{ANTHROPIC_MODEL}"
            except Exception as e:
                errors.append(f"anthropic: {e}")
                stream = None

        if stream is None:
            ans = _local_answer(question, ctx)
            if errors:
                ans = f"{ans}  [nlp fallback — errors: {' | '.join(errors)}]"
            yield event("start", provider="local-rule-based")
            yield event("token", content=ans)
            yield event("done")
            return

        yield event("start", provider=provider)
        try:
            got_any = False
            for chunk in stream:
                if chunk:
                    got_any = True
                    yield event("token", content=chunk)
            if not got_any:
                yield event("token", content=_local_answer(question, ctx))
            yield event("done")
        except Exception as e:
            yield event("error", message=str(e))
            yield event("token", content=_local_answer(question, ctx))
            yield event("done")

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _local_answer(question: str, ctx: Dict[str, Any]) -> str:
    """Fallback answers when Anthropic isn't configured."""
    q = question.lower()
    # Aggregate across all sessions
    totals: Dict[str, int] = {}
    worst = ("none", 1.0)
    for s in ctx.get("sets", []):
        totals[s["exercise"]] = totals.get(s["exercise"], 0) + int(s["reps"])
        if float(s["form_score"]) < worst[1]:
            worst = (s["exercise"], float(s["form_score"]))
    if "curl" in q:
        return f"You've done {totals.get('curl', 0)} curls across recent sessions."
    if "squat" in q:
        return f"You've done {totals.get('squat', 0)} squats across recent sessions."
    if "worst" in q and "form" in q:
        return (
            f"Worst form so far: {worst[0]} (score {worst[1]:.2f})."
            if worst[0] != "none"
            else "No sets recorded yet."
        )
    total = sum(totals.values())
    return f"Total reps across recent sessions: {total}. Breakdown: {totals}."


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "model": "keras" if not classifier.mock else "heuristic",
        "time": time.time(),
    }
