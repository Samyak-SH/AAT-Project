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

import json
import os
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LABELS = ["curl", "squat", "pushup", "rest"]
IDX_REST = LABELS.index("rest")
WINDOW = 50
CONFIDENCE_FORM_THRESHOLD = 0.85
RATE_LIMIT_PER_MIN = 100

BACKEND_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.environ.get("MODEL_PATH", BACKEND_DIR / "model.h5"))
DB_PATH = Path(os.environ.get("DB_PATH", BACKEND_DIR / "sessions.db"))

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
JWT_TTL_SECONDS = 24 * 3600

DEMO_USER = os.environ.get("DEMO_USER", "admin")
DEMO_PASS = os.environ.get("DEMO_PASS", "admin")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

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

    def predict(self, window: np.ndarray) -> Tuple[int, float, List[float]]:
        """window shape: (50, 3).  Returns (class_idx, confidence, all_probs)."""
        if self.model is not None:
            probs = self.model.predict(window[np.newaxis, ...], verbose=0)[0]
            probs = np.asarray(probs, dtype=np.float32)
            return int(np.argmax(probs)), float(np.max(probs)), probs.tolist()
        # ---------- Heuristic fallback ----------
        # Pick the class whose synthetic signature the window most resembles.
        ax, ay, az = window[:, 0], window[:, 1], window[:, 2]
        energy = float(np.std(ax) + np.std(ay) + np.std(az))
        if energy < 0.08:
            probs = [0.02, 0.02, 0.02, 0.94]
        else:
            # Dominant-axis fingerprint
            std_x, std_y, std_z = float(np.std(ax)), float(np.std(ay)), float(np.std(az))
            dom = int(np.argmax([std_x, std_y, std_z]))
            if dom == 1:
                probs = [0.75, 0.10, 0.10, 0.05]    # curl (y dominant)
            elif dom == 2:
                probs = [0.10, 0.70, 0.10, 0.10]    # squat (z dominant)
            else:
                probs = [0.10, 0.10, 0.75, 0.05]    # pushup (x dominant)
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
    where X is one of curl/squat/pushup. Each time the active exercise changes
    (e.g. curl -> squat) we open a new `set` row, so form score is per-set.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.session_id: Optional[int] = None
        self.current_exercise: Optional[str] = None
        self.last_active_exercise: Optional[str] = None
        self.state: str = "rest"            # "rest" or "active"
        self.reps: int = 0                  # reps for the current set
        self.hi_conf: int = 0
        self.total: int = 0
        self.set_id: Optional[int] = None
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
                    # Switch set if the active exercise changed
                    if exercise != "rest":
                        if self.current_exercise is None or (
                            self.current_exercise != exercise
                        ):
                            self._close_set_locked(conn)
                            self._open_set_locked(conn, exercise)

                    # Track form score counters for the current set
                    if self.set_id is not None:
                        self.total += 1
                        if confidence >= CONFIDENCE_FORM_THRESHOLD:
                            self.hi_conf += 1

                    # Rep state machine: active -> rest -> active-of-same
                    if exercise == "rest":
                        if self.state == "active":
                            self.state = "rest"
                    else:
                        if self.state == "rest" and self.last_active_exercise == exercise:
                            self.reps += 1
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
            self.last_prediction = {
                "exercise": exercise,
                "confidence": confidence,
                "reps": self.reps,
                "form_score": form_score,
                "session_id": self.session_id,
                "updated_at": now,
            }
            return dict(self.last_prediction)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.last_prediction)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


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
    token = authorization.split(None, 1)[1].strip()
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


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    token_type: str = "bearer"


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


@app.on_event("startup")
def _startup() -> None:
    global classifier, counter, rate_limiter
    db_init()
    classifier = Classifier(MODEL_PATH)
    counter = RepCounter()
    rate_limiter = PerDeviceRateLimiter(RATE_LIMIT_PER_MIN)
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
    if body.username != DEMO_USER or body.password != DEMO_PASS:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(token=make_token(body.username), expires_in=JWT_TTL_SECONDS)


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
def ingest(body: IngestRequest, user: str = Depends(require_auth)) -> IngestResponse:
    if not rate_limiter.check(body.device_id or "unknown"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    window = _validate_window(body)
    cls_idx, conf, probs = classifier.predict(window)
    exercise = LABELS[cls_idx]
    state = counter.update(exercise, conf)

    return IngestResponse(
        exercise=exercise,
        confidence=round(conf, 4),
        probabilities={LABELS[i]: round(float(probs[i]), 4) for i in range(len(LABELS))},
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


# ---- Live polling ----


@app.get("/api/live")
def live(user: str = Depends(require_auth)) -> Dict[str, Any]:
    snap = counter.snapshot()
    return {
        **snap,
        "model_mode": "heuristic" if classifier.mock else "keras",
    }


# ---- NLP query ----


def _current_context() -> Dict[str, Any]:
    """Build a compact session-data blob for Claude."""
    with db_connect() as conn:
        sessions = conn.execute(
            "SELECT id, user, started_at, ended_at FROM sessions "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
        sets = conn.execute(
            "SELECT session_id, exercise, reps, form_score, started_at, ended_at "
            "FROM sets WHERE session_id IN ("
            "  SELECT id FROM sessions ORDER BY id DESC LIMIT 10"
            ") ORDER BY session_id DESC, id ASC"
        ).fetchall()
    return {
        "now": time.time(),
        "live": counter.snapshot(),
        "sessions": [dict(s) for s in sessions],
        "sets": [dict(s) for s in sets],
    }


@app.post("/api/query", response_model=QueryResponse)
def query(body: QueryRequest, user: str = Depends(require_auth)) -> QueryResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    ctx = _current_context()
    ctx_json = json.dumps(ctx, default=str)

    if not ANTHROPIC_API_KEY:
        # Deterministic local answer when no API key is configured
        return QueryResponse(answer=_local_answer(question, ctx))

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            system=(
                "You are a concise gym coach assistant. Answer the user's question "
                "using ONLY the JSON session data provided. If the data is insufficient, "
                "say so briefly. Keep answers short."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Session data (JSON):\n{ctx_json}\n\n"
                        f"Question: {question}"
                    ),
                }
            ],
        )
        text = "".join(
            block.text for block in msg.content
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            text = _local_answer(question, ctx)
        return QueryResponse(answer=text)
    except Exception as exc:
        return QueryResponse(answer=f"(fallback) {_local_answer(question, ctx)}  [anthropic error: {exc}]")


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
    if "pushup" in q or "push-up" in q or "push up" in q:
        return f"You've done {totals.get('pushup', 0)} pushups across recent sessions."
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
