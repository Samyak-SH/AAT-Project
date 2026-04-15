"""
collect_data.py

Stand-alone FastAPI data-collection server. Receives windows posted by the
ESP32 firmware and appends each labelled window to a CSV file.

CSV columns:
    ax0..ax49, ay0..ay49, az0..az49, label

Usage:
    python collect_data.py --label curl
    python collect_data.py --label squat --out squat_dataset.csv --port 8000
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


WINDOW = 50
AXES = ("ax", "ay", "az")


class IngestPayload(BaseModel):
    device_id: str = Field(default="unknown")
    timestamp: int = 0
    ax: List[float]
    ay: List[float]
    az: List[float]


def build_header() -> List[str]:
    cols: List[str] = []
    for ax in AXES:
        cols.extend(f"{ax}{i}" for i in range(WINDOW))
    cols.append("label")
    return cols


def make_app(csv_path: Path, label: str) -> FastAPI:
    app = FastAPI(title="Gym Data Collector")
    lock = threading.Lock()
    state = {"count": 0}

    # Create the file with header if it doesn't exist
    if not csv_path.exists():
        with csv_path.open("w", newline="") as f:
            csv.writer(f).writerow(build_header())

    @app.get("/health")
    def health():
        return {"ok": True, "label": label, "samples": state["count"]}

    @app.post("/api/ingest")
    def ingest(p: IngestPayload):
        if len(p.ax) != WINDOW or len(p.ay) != WINDOW or len(p.az) != WINDOW:
            raise HTTPException(
                status_code=400,
                detail=f"Expected {WINDOW}-length ax/ay/az arrays",
            )
        row = list(p.ax) + list(p.ay) + list(p.az) + [label]
        with lock:
            with csv_path.open("a", newline="") as f:
                csv.writer(f).writerow(row)
            state["count"] += 1
            print(
                f"\r[{label}] collected windows: {state['count']}  "
                f"(device={p.device_id})",
                end="",
                flush=True,
            )
        return {"ok": True, "count": state["count"], "label": label}

    return app


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gym rep data collector")
    parser.add_argument(
        "--label",
        required=True,
        choices=["curl", "squat", "rest"],
        help="Label to attach to every window received during this run",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="CSV output path (default: sample_dataset.csv in this script's dir)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    out_path = Path(args.out) if args.out else Path(__file__).with_name("sample_dataset.csv")
    out_path = out_path.resolve()
    print(f"Writing windows to: {out_path}")
    print(f"Label: {args.label}")
    print(f"Listening on http://{args.host}:{args.port}/api/ingest")
    app = make_app(out_path, args.label)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
