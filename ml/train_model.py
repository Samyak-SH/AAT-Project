"""
train_model.py

Trains a small 1D-CNN on collected accelerometer windows.

Input CSV columns:
    ax0..ax49, ay0..ay49, az0..az49, label

Usage:
    python train_model.py --csv sample_dataset.csv --out model.h5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd


LABELS = ["curl", "squat", "rest"]
LABEL_TO_IDX = {name: i for i, name in enumerate(LABELS)}
WINDOW = 50
N_CLASSES = len(LABELS)


def load_dataset(csv_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(csv_path)
    unknown = set(df["label"].unique()) - set(LABELS)
    if unknown:
        raise ValueError(f"Unknown labels in {csv_path}: {unknown}")

    ax_cols = [f"ax{i}" for i in range(WINDOW)]
    ay_cols = [f"ay{i}" for i in range(WINDOW)]
    az_cols = [f"az{i}" for i in range(WINDOW)]

    ax = df[ax_cols].values.astype(np.float32)
    ay = df[ay_cols].values.astype(np.float32)
    az = df[az_cols].values.astype(np.float32)
    X = np.stack([ax, ay, az], axis=-1)          # (N, 50, 3)
    y = df["label"].map(LABEL_TO_IDX).values.astype(np.int64)
    return X, y


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, n: int) -> np.ndarray:
    cm = np.zeros((n, n), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm


def format_cm(cm: np.ndarray, labels: List[str]) -> str:
    header = "          " + "  ".join(f"{l:>7}" for l in labels)
    lines = [header]
    for i, row in enumerate(cm):
        lines.append(f"{labels[i]:>8}: " + "  ".join(f"{v:7d}" for v in row))
    return "\n".join(lines)


def build_model(input_shape: Tuple[int, int], n_classes: int):
    # Import TF lazily so the rest of the file is importable without it
    import tensorflow as tf
    from tensorflow.keras import layers, models

    model = models.Sequential(
        [
            layers.Input(shape=input_shape),
            layers.Conv1D(32, 5, padding="same", activation="relu"),
            layers.BatchNormalization(),
            layers.Conv1D(32, 5, padding="same", activation="relu"),
            layers.MaxPooling1D(2),
            layers.Conv1D(64, 3, padding="same", activation="relu"),
            layers.BatchNormalization(),
            layers.Conv1D(64, 3, padding="same", activation="relu"),
            layers.GlobalAveragePooling1D(),
            layers.Dropout(0.3),
            layers.Dense(64, activation="relu"),
            layers.Dense(n_classes, activation="softmax"),
        ]
    )
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def stratified_split(
    X: np.ndarray, y: np.ndarray, val_frac: float, seed: int
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    idx_train: List[int] = []
    idx_val: List[int] = []
    for c in range(N_CLASSES):
        idxs = np.where(y == c)[0]
        rng.shuffle(idxs)
        n_val = max(1, int(round(len(idxs) * val_frac))) if len(idxs) > 1 else 0
        idx_val.extend(idxs[:n_val].tolist())
        idx_train.extend(idxs[n_val:].tolist())
    rng.shuffle(idx_train)
    rng.shuffle(idx_val)
    train_arr = np.array(idx_train, dtype=np.int64)
    val_arr = np.array(idx_val, dtype=np.int64)
    return X[train_arr], y[train_arr], X[val_arr], y[val_arr]


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train gym rep CNN")
    parser.add_argument(
        "--csv",
        default=str(Path(__file__).with_name("sample_dataset.csv")),
        help="Path to the CSV dataset",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "backend" / "model.h5"),
        help="Output path for trained Keras model",
    )
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-frac", type=float, default=0.2)
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    csv_path = Path(args.csv).resolve()
    out_path = Path(args.out).resolve()
    print(f"Loading dataset: {csv_path}")
    X, y = load_dataset(csv_path)
    print(f"  dataset: X={X.shape}, y={y.shape}")
    if len(X) < N_CLASSES:
        print("Not enough rows to train. Collect more data first.", file=sys.stderr)
        return 2

    X_train, y_train, X_val, y_val = stratified_split(X, y, args.val_frac, args.seed)
    print(f"  train: {X_train.shape}, val: {X_val.shape}")

    model = build_model((WINDOW, 3), N_CLASSES)
    model.summary()

    model.fit(
        X_train,
        y_train,
        validation_data=(X_val, y_val) if len(X_val) else None,
        epochs=args.epochs,
        batch_size=min(args.batch, max(1, len(X_train))),
        verbose=2,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(out_path)
    print(f"Saved model: {out_path}")

    # Evaluation on val split (fallback to train if val is empty)
    X_eval, y_eval = (X_val, y_val) if len(X_val) else (X_train, y_train)
    y_pred = np.argmax(model.predict(X_eval, verbose=0), axis=1)
    acc = float(np.mean(y_pred == y_eval))
    cm = confusion_matrix(y_eval, y_pred, N_CLASSES)
    print(f"\nEval accuracy: {acc:.4f}")
    print("Confusion matrix (rows=true, cols=pred):")
    print(format_cm(cm, LABELS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
