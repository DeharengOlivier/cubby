#!/bin/sh
# Install cubby on any Unix (macOS or Linux).
#
#   ./install.sh                 install the CLI only
#   ./install.sh --service       install the CLI and start the background agent
#   ./install.sh --service --delay 2m --source ~/Downloads
#
# Uses pipx when available, otherwise a self-contained venv in
# ~/.local/share/cubby with a launcher symlinked into ~/.local/bin.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${HOME}/.local"
VENV_DIR="${PREFIX}/share/cubby/venv"
BIN_DIR="${PREFIX}/bin"
WANT_SERVICE=0
SERVICE_ARGS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --service) WANT_SERVICE=1 ;;
        --delay|--source|--interval|--config)
            SERVICE_ARGS="${SERVICE_ARGS} $1 $2"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# --- Python version check (need 3.11+) ------------------------------------
PYTHON=${PYTHON:-python3}
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: python3 not found" >&2; exit 1
fi
"$PYTHON" - <<'PY' || { echo "error: cubby needs Python 3.11+" >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

# --- Install the package ---------------------------------------------------
if command -v pipx >/dev/null 2>&1; then
    echo "Installing cubby with pipx..."
    pipx install --force "$REPO_DIR"
    CUBBY_BIN="$(command -v cubby)"
else
    echo "pipx not found; installing into a venv at ${VENV_DIR}"
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet "$REPO_DIR"
    mkdir -p "$BIN_DIR"
    ln -sf "$VENV_DIR/bin/cubby" "$BIN_DIR/cubby"
    CUBBY_BIN="$BIN_DIR/cubby"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "note: add ${BIN_DIR} to your PATH to run 'cubby' directly" ;;
    esac
fi

echo "Installed: ${CUBBY_BIN}"
"$CUBBY_BIN" --version

# --- Optionally register the background agent ------------------------------
if [ "$WANT_SERVICE" -eq 1 ]; then
    # shellcheck disable=SC2086
    "$CUBBY_BIN" install $SERVICE_ARGS
fi

echo "Done. Try: cubby plan"
