"""
train_model.py  — Smart Gym Rep Counter & Form Checker

Trains a CNN-LSTM hybrid on the RecoFit dataset (Microsoft, 50 Hz, arm-worn,
94 subjects, 75 exercise types).  Falls back to the bundled sample CSV if no
real dataset is found.

Dataset source:
  https://github.com/microsoft/Exercise-Recognition-from-Wearable-Sensors
  exercise_data.50.0000_singleonly.mat  (auto-downloaded if absent)

Output: backend/model.h5   (Keras SavedModel, hot-reloaded by server.py)

Usage:
    python train_model.py                          # auto-download + train
    python train_model.py --mat path/to/file.mat  # explicit dataset path
    python train_model.py --csv sample_dataset.csv # legacy CSV path
    python train_model.py --epochs 60 --batch 64  # override hyperparams
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

LABELS: List[str] = ["curl", "squat", "rest", "other"]
LABEL_TO_IDX: Dict[str, int] = {n: i for i, n in enumerate(LABELS)}
N_CLASSES: int = len(LABELS)
WINDOW: int = 50          # samples (= 1 second at 50 Hz)
STRIDE: int = 25          # 50 % overlap
FS: int = 50              # target sampling frequency (Hz)

# Max windows to sample from the "other" class to keep balance.
# "other" has 60k+ windows available — we cap it to avoid dominating.
MAX_OTHER_WINDOWS: int = 12000
# "rest" class: only real worn-sensor rest (no "Device on Table" — 51k
# windows of a sensor sitting on a table is unrealistic for a wrist device).
MAX_REST_WINDOWS: int = 10000

# RecoFit exercise-index → our label
# accelDataMatrix columns: [timestamp, ax, ay, az]  (g-units, 50 Hz)
RECOFIT_MAP: Dict[int, str] = {
    # ----- curl -----
    4:  "curl",   # Bicep Curl
    5:  "curl",   # Biceps Curl (band)
    67: "curl",   # Two-arm Dumbbell Curl (both arms)
    74: "curl",   # Alternating Dumbbell Curl
    # ----- squat -----
    50: "squat",  # Squat
    51: "squat",  # Squat (arms in front)
    52: "squat",  # Squat (hands behind head)
    53: "squat",  # Squat (kettlebell / goblet)
    54: "squat",  # Squat Jump
    # ----- rest (real worn-sensor rest only, no "Device on Table") -----
    40: "rest",   # Rest
    31: "rest",   # Non-Exercise
    # ----- other (everything else — keeps model from defaulting to squat) ---
    37: "other",  # Pushup (knee or foot variation)
    38: "other",  # Pushups
    29: "other",  # Lunge (alternating)
    71: "other",  # Walking lunge
    45: "other",  # Shoulder Press (dumbbell)
    55: "other",  # Squat Rack Shoulder Press
    48: "other",  # Sit-up
    49: "other",  # Sit-ups
    14: "other",  # Dumbbell Row (knee on bench)
    13: "other",  # Dumbbell Deadlift Row
    10: "other",  # Crunch
    43: "other",  # Russian Twist
    70: "other",  # Walk
     7: "other",  # Burpee
    23: "other",  # Jumping Jacks
    24: "other",  # Kettlebell Swing
    25: "other",  # Lateral Raise
    22: "other",  # Jump Rope
}

RECOFIT_URL = (
    "https://github.com/microsoft/Exercise-Recognition-from-Wearable-Sensors"
    "/raw/main/exercise_data.50.0000_singleonly.mat"
)
DEFAULT_MAT = Path(__file__).parent / "datasets" / "recofit_single.mat"


# ──────────────────────────────────────────────
# GPU setup
# ──────────────────────────────────────────────
def _setup_gpu() -> None:
    """Point TF at the CUDA libs bundled inside the venv."""
    venv_root = Path(sys.executable).parent.parent
    nvidia_root = venv_root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "nvidia"
    lib_paths: List[str] = []
    if nvidia_root.exists():
        for subdir in nvidia_root.iterdir():
            lib_dir = subdir / "lib"
            if lib_dir.exists():
                lib_paths.append(str(lib_dir))
    if lib_paths:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = ":".join(lib_paths + ([existing] if existing else []))


_setup_gpu()  # must run before `import tensorflow`


# ──────────────────────────────────────────────
# Dataset loading
# ──────────────────────────────────────────────
def _download_mat(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading RecoFit dataset (~1.5 GB) → {dest}")
    print("  source:", RECOFIT_URL)

    def _progress(block_num: int, block_size: int, total: int) -> None:
        downloaded = block_num * block_size
        if total > 0:
            pct = min(100, downloaded * 100 // total)
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct:3d}%  {downloaded/1e6:.1f}/{total/1e6:.0f} MB", end="", flush=True)

    urllib.request.urlretrieve(RECOFIT_URL, dest, _progress)
    print()


def _extract_windows_mat(mat_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Parse RecoFit .mat → (X, y) windows."""
    try:
        import scipy.io
    except ImportError:
        raise RuntimeError("scipy not installed: pip install scipy")

    print(f"Loading RecoFit .mat: {mat_path}  ({mat_path.stat().st_size/1e6:.0f} MB)")
    mat = scipy.io.loadmat(str(mat_path))
    sd = mat["subject_data"]                   # (94, 75) object array
    n_subjects, n_exercises = sd.shape
    print(f"  subjects={n_subjects}, exercise_types={n_exercises}")

    # Collect per-label so we can cap "other" and "rest"
    per_label_windows: Dict[str, List[np.ndarray]] = {l: [] for l in LABELS}

    for exercise_idx, label_name in RECOFIT_MAP.items():
        if exercise_idx >= n_exercises:
            continue
        count_before = len(per_label_windows[label_name])
        for subj in range(n_subjects):
            cell = sd[subj, exercise_idx]
            if cell.size == 0:
                continue
            try:
                inner = cell[0, 0]
                data_struct = inner["data"][0, 0]
                accel = data_struct["accelDataMatrix"]  # (N, 4): [t, ax, ay, az]
                if accel.shape[0] < WINDOW:
                    continue
                xyz = accel[:, 1:4].astype(np.float32)
                for start in range(0, len(xyz) - WINDOW + 1, STRIDE):
                    per_label_windows[label_name].append(xyz[start : start + WINDOW])
            except (KeyError, IndexError, ValueError):
                continue
        added = len(per_label_windows[label_name]) - count_before
        print(f"  {label_name:6s} (ex{exercise_idx:02d}): +{added} windows")

    # Apply caps to keep class balance
    rng_cap = np.random.default_rng(42)
    for lbl, cap in [("other", MAX_OTHER_WINDOWS), ("rest", MAX_REST_WINDOWS)]:
        if len(per_label_windows[lbl]) > cap:
            idx = rng_cap.choice(len(per_label_windows[lbl]), cap, replace=False)
            per_label_windows[lbl] = [per_label_windows[lbl][i] for i in sorted(idx)]
            print(f"  {lbl:6s}: capped to {cap:,} windows")

    print("\nFinal per-class counts:")
    windows: List[np.ndarray] = []
    labels: List[int] = []
    for lbl in LABELS:
        wins = per_label_windows[lbl]
        label_idx = LABEL_TO_IDX[lbl]
        windows.extend(wins)
        labels.extend([label_idx] * len(wins))
        print(f"  {lbl:8s}: {len(wins):6,} windows")

    if not windows:
        raise RuntimeError("No windows extracted — check dataset file.")

    X = np.stack(windows, axis=0)        # (N, 50, 3)
    y = np.array(labels, dtype=np.int64) # (N,)
    return X, y


def _extract_windows_csv(csv_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Legacy: load from flat CSV (ax0..ax49, ay0..ay49, az0..az49, label)."""
    import pandas as pd
    df = pd.read_csv(csv_path)
    unknown = set(df["label"].unique()) - set(LABELS)
    if unknown:
        raise ValueError(f"Unknown labels in CSV: {unknown}")
    ax = df[[f"ax{i}" for i in range(WINDOW)]].values.astype(np.float32)
    ay = df[[f"ay{i}" for i in range(WINDOW)]].values.astype(np.float32)
    az = df[[f"az{i}" for i in range(WINDOW)]].values.astype(np.float32)
    X = np.stack([ax, ay, az], axis=-1)  # (N, 50, 3) — axes last
    y = df["label"].map(LABEL_TO_IDX).values.astype(np.int64)
    return X, y


# ──────────────────────────────────────────────
# Preprocessing
# ──────────────────────────────────────────────
def normalise(X: np.ndarray) -> np.ndarray:
    """Per-window, per-channel z-score normalisation."""
    mu = X.mean(axis=1, keepdims=True)           # (N, 1, 3)
    sd = X.std(axis=1, keepdims=True) + 1e-8
    return (X - mu) / sd


def augment(X: np.ndarray, y: np.ndarray, seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    """Simple augmentation: Gaussian noise + axis jitter."""
    rng = np.random.default_rng(seed)
    noisy_X = X + rng.normal(0, 0.02, X.shape).astype(np.float32)
    flipped_X = X * rng.choice([-1, 1], size=(len(X), 1, 3)).astype(np.float32)
    X_aug = np.concatenate([X, noisy_X, flipped_X], axis=0)
    y_aug = np.concatenate([y, y, y], axis=0)
    idx = rng.permutation(len(X_aug))
    return X_aug[idx], y_aug[idx]


def stratified_split(
    X: np.ndarray,
    y: np.ndarray,
    val_frac: float,
    seed: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    idx_train, idx_val = [], []
    for c in range(N_CLASSES):
        idxs = np.where(y == c)[0]
        rng.shuffle(idxs)
        n_val = max(1, int(len(idxs) * val_frac)) if len(idxs) > 1 else 0
        idx_val.extend(idxs[:n_val])
        idx_train.extend(idxs[n_val:])
    rng.shuffle(idx_train)
    rng.shuffle(idx_val)
    tr, va = np.array(idx_train), np.array(idx_val)
    return X[tr], y[tr], X[va], y[va]


# ──────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────
def build_model(input_shape: Tuple[int, int], n_classes: int):
    """CNN-LSTM hybrid — better than pure CNN for periodic motion."""
    import tensorflow as tf
    from tensorflow.keras import layers, models, regularizers

    l2 = regularizers.l2(1e-4)

    inp = layers.Input(shape=input_shape, name="accel_input")

    # ── CNN block 1 ──
    x = layers.Conv1D(64, 5, padding="same", activation="relu",
                      kernel_regularizer=l2, name="conv1")(inp)
    x = layers.BatchNormalization()(x)
    x = layers.Conv1D(64, 5, padding="same", activation="relu",
                      kernel_regularizer=l2, name="conv2")(x)
    x = layers.MaxPooling1D(2, name="pool1")(x)

    # ── CNN block 2 ──
    x = layers.Conv1D(128, 3, padding="same", activation="relu",
                      kernel_regularizer=l2, name="conv3")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Conv1D(128, 3, padding="same", activation="relu",
                      kernel_regularizer=l2, name="conv4")(x)
    x = layers.MaxPooling1D(2, name="pool2")(x)

    # ── Bidirectional LSTM ──
    x = layers.Bidirectional(
        layers.LSTM(128, return_sequences=True, dropout=0.2), name="blstm1"
    )(x)
    x = layers.Bidirectional(
        layers.LSTM(64, dropout=0.2), name="blstm2"
    )(x)

    # ── Classifier head ──
    x = layers.Dense(128, activation="relu", kernel_regularizer=l2, name="fc1")(x)
    x = layers.Dropout(0.4, name="drop")(x)
    out = layers.Dense(n_classes, activation="softmax", name="predictions")(x)

    model = models.Model(inp, out, name="gym_cnn_lstm")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


# ──────────────────────────────────────────────
# Evaluation helpers
# ──────────────────────────────────────────────
def confusion_matrix_manual(y_true: np.ndarray, y_pred: np.ndarray, n: int) -> np.ndarray:
    cm = np.zeros((n, n), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm


def format_cm(cm: np.ndarray, labels: List[str]) -> str:
    header = "          " + "  ".join(f"{l:>7}" for l in labels)
    lines = [header]
    for i, row in enumerate(cm):
        pct = row[i] / row.sum() * 100 if row.sum() else 0
        lines.append(f"{labels[i]:>8}: " + "  ".join(f"{v:7d}" for v in row) + f"  ({pct:.0f}% correct)")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train Smart Gym CNN-LSTM model")
    p.add_argument("--mat",    default=str(DEFAULT_MAT),    help=".mat dataset path")
    p.add_argument("--csv",    default=None,                help="Legacy CSV dataset (overrides --mat)")
    p.add_argument("--out",    default=str(Path(__file__).resolve().parents[1] / "backend" / "model.h5"))
    p.add_argument("--epochs", type=int,   default=50)
    p.add_argument("--batch",  type=int,   default=64)
    p.add_argument("--seed",   type=int,   default=42)
    p.add_argument("--val-frac", type=float, default=0.2)
    p.add_argument("--no-augment", action="store_true", help="Skip data augmentation")
    p.add_argument("--download", action="store_true",
                   help="Force re-download of RecoFit dataset")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    out_path = Path(args.out).resolve()

    # ── Load dataset ──────────────────────────
    if args.csv:
        print(f"Using CSV dataset: {args.csv}")
        X, y = _extract_windows_csv(Path(args.csv))
    else:
        mat_path = Path(args.mat)
        if args.download or not mat_path.exists():
            _download_mat(mat_path)
        X, y = _extract_windows_mat(mat_path)

    print(f"\nRaw windows: X={X.shape}, y={y.shape}")
    for i, lbl in enumerate(LABELS):
        count = int((y == i).sum())
        print(f"  {lbl:8s}: {count:6d} windows")

    if len(X) < N_CLASSES * 10:
        print("ERROR: Too few samples to train.", file=sys.stderr)
        return 2

    # ── Normalise ─────────────────────────────
    X = normalise(X)

    # ── Augment (3× data) ─────────────────────
    if not args.no_augment:
        X, y = augment(X, y, seed=args.seed)
        print(f"\nAfter augmentation: X={X.shape}, y={y.shape}")

    # ── Split ─────────────────────────────────
    X_tr, y_tr, X_va, y_va = stratified_split(X, y, args.val_frac, args.seed)
    print(f"Train: {X_tr.shape}   Val: {X_va.shape}")

    # ── GPU info ──────────────────────────────
    import tensorflow as tf
    gpus = tf.config.list_physical_devices("GPU")
    if gpus:
        for g in gpus:
            detail = tf.config.experimental.get_device_details(g)
            print(f"\nGPU: {detail.get('device_name', g.name)}")
        for g in gpus:
            tf.config.experimental.set_memory_growth(g, True)
    else:
        print("\nNo GPU detected — training on CPU (slower)")

    # ── Build & summarise model ───────────────
    model = build_model((WINDOW, 3), N_CLASSES)
    model.summary()

    # ── Callbacks ─────────────────────────────
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_accuracy", patience=10, restore_best_weights=True, verbose=1
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=5, min_lr=1e-6, verbose=1
        ),
        tf.keras.callbacks.ModelCheckpoint(
            str(out_path) + ".best.keras",
            save_best_only=True, monitor="val_accuracy", verbose=0
        ),
    ]

    # ── Train ─────────────────────────────────
    print(f"\nTraining for up to {args.epochs} epochs (batch={args.batch}) …")
    history = model.fit(
        X_tr, y_tr,
        validation_data=(X_va, y_va),
        epochs=args.epochs,
        batch_size=args.batch,
        callbacks=callbacks,
        verbose=2,
    )

    # ── Save ──────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(out_path)
    print(f"\nModel saved → {out_path}")

    # ── Evaluate ──────────────────────────────
    y_pred = np.argmax(model.predict(X_va, verbose=0), axis=1)
    acc = float(np.mean(y_pred == y_va))
    cm = confusion_matrix_manual(y_va, y_pred, N_CLASSES)
    best_val = max(history.history.get("val_accuracy", [0]))

    print(f"\n{'='*60}")
    print(f"  Val accuracy   : {acc*100:.2f}%")
    print(f"  Best epoch acc : {best_val*100:.2f}%")
    print(f"\nConfusion matrix (rows=true, cols=predicted):")
    print(format_cm(cm, LABELS))
    print(f"{'='*60}")

    print("\nPer-class metrics:")
    for i, lbl in enumerate(LABELS):
        tp = cm[i, i]
        fn = cm[i, :].sum() - tp
        fp = cm[:, i].sum() - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        print(f"  {lbl:8s}  precision={prec:.3f}  recall={rec:.3f}  F1={f1:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
