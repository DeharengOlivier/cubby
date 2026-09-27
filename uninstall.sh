#!/bin/sh
# Remove cubby: stop the agent, then uninstall the CLI.
set -eu
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail

PREFIX="${HOME}/.local"
VENV_DIR="${PREFIX}/share/cubby/venv"
BIN_LINK="${PREFIX}/bin/cubby"

# Stop and remove the background agent first (ignore if not installed).
if command -v cubby >/dev/null 2>&1; then
    if ! cubby uninstall; then
        echo "warning: could not remove the background agent; see 'cubby status'" >&2
    fi
fi

if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^cubby-sort '; then
    pipx uninstall cubby-sort
fi

if [ -e "$BIN_LINK" ]; then
    rm -f "$BIN_LINK"
fi
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
fi

echo "cubby removed. Your sorted folders and config (~/.config/cubby) are untouched."
