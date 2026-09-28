#!/bin/sh
# Remove cubby: stop the agent, then uninstall the CLI.
set -eu
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail

PREFIX="${HOME}/.local"
VENV_DIR="${PREFIX}/share/cubby/venv"
BIN_LINK="${PREFIX}/bin/cubby"

FORCE=0
if [ "${1:-}" = "--force" ]; then
    FORCE=1
fi

# Stop and remove the background agent first. When cubby says it could not
# stop the agent (exit 1), keep the CLI: without it there is no 'cubby' left to
# stop the agent with. Any other failure (a broken install whose cubby cannot
# even start) must not make the install impossible to remove.
if command -v cubby >/dev/null 2>&1; then
    status=0
    cubby uninstall || status=$?
    if [ "$status" -eq 1 ] && [ "$FORCE" -eq 0 ]; then
        echo "error: the background agent could not be stopped; cubby was NOT removed." >&2
        echo "Stop it as docs/RUNBOOK.md section 1 says, then run this script again" >&2
        echo "(or run it with --force to remove cubby anyway)." >&2
        exit 1
    fi
    if [ "$status" -ne 0 ]; then
        echo "warning: 'cubby uninstall' failed (exit $status); removing cubby anyway." >&2
        echo "Check that no agent is left: see docs/RUNBOOK.md section 1." >&2
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
