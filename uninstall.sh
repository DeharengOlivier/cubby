#!/bin/sh
# Remove cubby: stop the agent, then uninstall the CLI.
set -eu

PREFIX="${HOME}/.local"
VENV_DIR="${PREFIX}/share/cubby/venv"
BIN_LINK="${PREFIX}/bin/cubby"

# Stop and remove the background agent first (ignore if not installed).
if command -v cubby >/dev/null 2>&1; then
    cubby uninstall || true
fi

if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q cubby; then
    pipx uninstall cubby-sort || true
fi

if [ -e "$BIN_LINK" ]; then
    rm -f "$BIN_LINK"
fi
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
fi

echo "cubby removed. Your sorted folders and config (~/.config/cubby) are untouched."
