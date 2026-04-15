"""
simulate_device.py — pretend to be an ESP32+ADXL345 and exercise the backend.

What it does
------------
1. Logs in with demo creds → gets a JWT.
2. Starts a session.
3. Streams synthetic 50-sample windows at 2 Hz (same cadence as the firmware's
   25-sample stride @ 50 Hz) for a scripted routine:
       10s curl, 3s rest, 10s squat, 3s rest, 10s pushup, 3s rest
   The synthetic waveforms are intentionally shaped so the default heuristic
   classifier in the backend picks the right class even without a trained model.
4. Ends the session and prints the summary.

Usage
-----
    # start the backend first (docker compose up / make up)
    python tools/simulate_device.py
    # options:
    python tools/simulate_device.py --url http://localhost:8000 \
        --user admin --password admin --device sim-01 --routine default
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from typing import Dict, List, Tuple
from urllib import error as urlerr
from urllib import request as urlreq
import json


WINDOW = 50
HZ = 50.0
STRIDE_SEC = WINDOW / HZ / 2.0   # emit every 0.5s (25-sample stride at 50Hz)


def synth_window(exercise: str, phase: float) -> Tuple[List[float], List[float], List[float]]:
    """Return (ax, ay, az) lists of length WINDOW for a given label.

    Shapes are aligned with the heuristic fallback in server.py so the demo
    works even without a trained model.h5:
        curl   → dominant Y-axis oscillation
        squat  → dominant Z-axis oscillation
        pushup → dominant X-axis oscillation
        rest   → low-energy around gravity on Z
    """
    ax: List[float] = []
    ay: List[float] = []
    az: List[float] = []
    if exercise == "curl":
        for t in range(WINDOW):
            ax.append(0.05 * random.gauss(0, 1))
            ay.append(0.9 * math.sin(2 * math.pi * t / WINDOW + phase) + 0.03 * random.gauss(0, 1))
            az.append(1.0 + 0.3 * math.cos(2 * math.pi * t / WINDOW + phase) + 0.03 * random.gauss(0, 1))
    elif exercise == "squat":
        for t in range(WINDOW):
            ax.append(0.2 * math.sin(2 * math.pi * t / WINDOW + phase) + 0.04 * random.gauss(0, 1))
            ay.append(0.1 * random.gauss(0, 1))
            az.append(1.0 + 0.7 * math.sin(2 * math.pi * t / WINDOW + phase) + 0.04 * random.gauss(0, 1))
    else:  # rest
        for _ in range(WINDOW):
            ax.append(0.02 * random.gauss(0, 1))
            ay.append(0.02 * random.gauss(0, 1))
            az.append(1.0 + 0.02 * random.gauss(0, 1))
    return ax, ay, az


def http_json(method: str, url: str, token: str = "", body: Dict | None = None) -> Dict:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urlreq.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlreq.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urlerr.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} from {method} {url}: {e.read().decode('utf-8', 'ignore')}")
    except urlerr.URLError as e:
        raise SystemExit(f"Cannot reach {url}: {e}")


def routine_default() -> List[Tuple[str, float]]:
    """Scripted sequence (exercise, duration_seconds)."""
    return [
        ("curl", 10), ("rest", 3),
        ("squat", 10), ("rest", 3),
    ]


def routine_quick() -> List[Tuple[str, float]]:
    return [("curl", 4), ("rest", 2), ("squat", 4), ("rest", 2)]


ROUTINES = {"default": routine_default, "quick": routine_quick}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Simulate an ESP32 streaming to the backend")
    ap.add_argument("--url", default="http://localhost:8000", help="Backend base URL")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--device", default="sim-01")
    ap.add_argument(
        "--routine",
        default="default",
        choices=list(ROUTINES.keys()),
        help="Scripted exercise sequence",
    )
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument(
        "--loop",
        action="store_true",
        help="Keep replaying the routine forever (Ctrl-C to stop).",
    )
    ap.add_argument(
        "--attach",
        action="store_true",
        help="Don't start/end a session — just stream into whatever session "
             "you've started from the UI. Use this when you want the "
             "dashboard's Start/End buttons to drive the session.",
    )
    ap.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier. 0.5 = half speed (slower, easier to "
             "watch on the dashboard). Default 1.0 = firmware cadence (2 Hz).",
    )
    args = ap.parse_args(argv)
    random.seed(args.seed)
    stride = STRIDE_SEC / max(0.1, args.speed)

    base = args.url.rstrip("/")

    print(f"→ Logging in as {args.user} at {base}")
    login = http_json("POST", f"{base}/api/auth/login",
                      body={"username": args.user, "password": args.password})
    token = login["token"]
    print(f"   got token (expires in {login['expires_in']}s)")

    session_id = None
    if args.attach:
        print("→ Attach mode: not starting a session (use the UI's Start button)")
    else:
        print("→ Starting session")
        sess = http_json("POST", f"{base}/api/session/start", token=token)
        session_id = sess["session_id"]
        print(f"   session_id={session_id}")

    steps = ROUTINES[args.routine]()
    phase = 0.0
    total_windows = 0
    try:
        pass_num = 0
        while True:
            pass_num += 1
            if args.loop:
                print(f"\n── pass {pass_num} ──────────────────────────────────")
            for exercise, duration in steps:
                n = max(1, int(round(duration / STRIDE_SEC)))
                print(f"→ {exercise.upper()} for {duration}s ({n} windows @ {args.speed}x)")
                for _ in range(n):
                    ax, ay, az = synth_window(exercise, phase)
                    phase += 0.6                         # nudge phase between reps
                    payload = {
                        "device_id": args.device,
                        "timestamp": int(time.time()),
                        "ax": ax, "ay": ay, "az": az,
                    }
                    r = http_json("POST", f"{base}/api/ingest", token=token, body=payload)
                    total_windows += 1
                    print(
                        f"   [{total_windows:04d}] pred={r['exercise']:<6} "
                        f"conf={r['confidence']:.2f}  reps={r['reps']}  "
                        f"form={r['form_score']:.2f}  session={r['session_id']}"
                    )
                    time.sleep(stride)
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\n(interrupted)")

    if args.attach:
        print("→ Attach mode: leaving session for the UI to end.")
        return 0

    print("→ Ending session")
    end = http_json("POST", f"{base}/api/session/end", token=token)
    summary = http_json("GET", f"{base}/api/session/{end['session_id']}", token=token)
    print("\n── SESSION SUMMARY ─────────────────────────────")
    print(f"id:            {summary['id']}")
    print(f"total_reps:    {summary['total_reps']}")
    print(f"reps_per_ex:   {summary['reps_per_exercise']}")
    print(f"form_per_ex:   {summary['form_per_exercise']}")
    print(f"sets:          {len(summary['sets'])}")
    for s in summary["sets"]:
        print(
            f"  - set #{s['id']}: {s['exercise']:<6} "
            f"reps={s['reps']:<3} form={s['form_score']:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
