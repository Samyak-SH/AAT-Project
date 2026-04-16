#!/usr/bin/env bash
# train_gpu.sh — run train_model.py with CUDA libs from the bundled venv
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_ROOT/ml_env"

# Build LD_LIBRARY_PATH from all nvidia/*/lib dirs bundled inside the venv
CUDA_LIBS=""
for d in "$VENV"/lib/python*/site-packages/nvidia/*/lib; do
    [ -d "$d" ] && CUDA_LIBS="$d${CUDA_LIBS:+:$CUDA_LIBS}"
done

export LD_LIBRARY_PATH="${CUDA_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$VENV/bin/python" "$SCRIPT_DIR/train_model.py" "$@"
