#!/usr/bin/env bash
# reco bootstrap (bash): detect Python >= 3.12, create .venv, install uv inside it,
# editable-install the seven workspace packages, then run the full pytest suite.
# Idempotent: re-running only fills in missing steps. Caches stay inside .venv.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV_DIR="$ROOT/.venv"

log() { printf '%s\n' "$*"; }

python_ok() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)' >/dev/null 2>&1
}

find_python() {
    if [ -n "${RECO_PYTHON:-}" ] && python_ok "$RECO_PYTHON"; then
        printf '%s\n' "$RECO_PYTHON"
        return 0
    fi
    local dsh="$HOME/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe"
    if [ -x "$dsh" ] && python_ok "$dsh"; then
        printf '%s\n' "$dsh"
        return 0
    fi
    local name
    for name in python3.12 python3 python; do
        if command -v "$name" >/dev/null 2>&1 && python_ok "$name"; then
            command -v "$name"
            return 0
        fi
    done
    return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
    log "ERROR: no Python >= 3.12 found; set RECO_PYTHON to an interpreter path." >&2
    exit 1
fi
log "[1/5] Python interpreter: $PY"
log "      version: $("$PY" -c 'import sys; print(sys.version.split()[0])')"

venv_python() {
    if [ -x "$VENV_DIR/Scripts/python.exe" ]; then
        printf '%s\n' "$VENV_DIR/Scripts/python.exe"
    elif [ -x "$VENV_DIR/bin/python" ]; then
        printf '%s\n' "$VENV_DIR/bin/python"
    fi
}

VENV_PY="$(venv_python)"
if [ -n "$VENV_PY" ]; then
    log "[2/5] Reusing existing venv: $VENV_DIR"
else
    log "[2/5] Creating venv: $VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
    VENV_PY="$(venv_python)"
    if [ -z "$VENV_PY" ]; then
        log "ERROR: venv created but no interpreter found under $VENV_DIR" >&2
        exit 1
    fi
fi

export UV_CACHE_DIR="${UV_CACHE_DIR:-$VENV_DIR/.uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

UV_BIN=""
if [ -x "$VENV_DIR/Scripts/uv.exe" ]; then
    UV_BIN="$VENV_DIR/Scripts/uv.exe"
elif [ -x "$VENV_DIR/bin/uv" ]; then
    UV_BIN="$VENV_DIR/bin/uv"
fi
if [ -z "$UV_BIN" ]; then
    log "[3/5] Installing uv into .venv (not system-wide)"
    "$VENV_PY" -m pip install --no-cache-dir --disable-pip-version-check uv
    if [ -x "$VENV_DIR/Scripts/uv.exe" ]; then
        UV_BIN="$VENV_DIR/Scripts/uv.exe"
    elif [ -x "$VENV_DIR/bin/uv" ]; then
        UV_BIN="$VENV_DIR/bin/uv"
    else
        log "ERROR: uv installation did not produce a launcher under $VENV_DIR" >&2
        exit 1
    fi
else
    log "[3/5] uv already installed in .venv"
fi

log "[4/5] Syncing the locked workspace and dev dependencies"
"$UV_BIN" sync --frozen --inexact --python "$VENV_PY"

log "[5/5] Running pytest (repo root)"
"$VENV_PY" -m pytest

log "Bootstrap complete: $VENV_PY"
