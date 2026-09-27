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
# pipefail is POSIX 2024 and supported by dash, bash and zsh; older shells skip it.
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${HOME}/.local"
VENV_DIR="${PREFIX}/share/cubby/venv"
BIN_DIR="${PREFIX}/bin"
WANT_SERVICE=0

# Keep the agent's options as positional parameters rather than a string, so a
# folder such as "~/My Downloads" reaches `cubby install` as one argument.
remaining=$#
while [ "$remaining" -gt 0 ]; do
    case "$1" in
        --service)
            WANT_SERVICE=1; shift; remaining=$((remaining - 1)) ;;
        --delay|--source|--interval|--config)
            if [ "$remaining" -lt 2 ]; then
                echo "error: $1 needs a value" >&2; exit 2
            fi
            option=$1; value=$2; shift 2; remaining=$((remaining - 2))
            set -- "$@" "$option" "$value" ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
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
    "$CUBBY_BIN" install "$@"
fi

echo "Done. Try: cubby plan"
