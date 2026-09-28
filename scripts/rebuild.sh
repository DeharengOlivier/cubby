#!/bin/sh
# Check that the wheel and sdist of the committed tree are reproducible.
#
# Builds HEAD twice from a clean export, the second time with another umask,
# time zone, locale and file dates, and fails unless both builds are byte
# identical. With --against SUMS (a SHA256SUMS file, such as the one attached
# to a GitHub release), the rebuild must also match those sums: that is how
# anyone checks a release was built from the tagged source.
#
#   scripts/rebuild.sh
#   git checkout v0.3.0 && gh release download v0.3.0 -p SHA256SUMS
#   scripts/rebuild.sh --against SHA256SUMS
set -eu
# shellcheck disable=SC3040
(set -o pipefail 2>/dev/null) && set -o pipefail

usage() {
    echo "usage: scripts/rebuild.sh [--against SHA256SUMS]" >&2
    exit 2
}

AGAINST=""
case "${1:-}" in
    "") ;;
    --against)
        [ $# -eq 2 ] || usage
        AGAINST="$2"
        [ -f "$AGAINST" ] || { echo "error: no such file: $AGAINST" >&2; exit 2; }
        AGAINST="$(cd "$(dirname "$AGAINST")" && pwd)/$(basename "$AGAINST")"
        ;;
    *) usage ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$@"; }
else
    sha256() { shasum -a 256 "$@"; }  # macOS
fi

# A release is built without it; set here, it would change every date inside.
unset SOURCE_DATE_EPOCH

ROOT="$(git rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

git -C "$ROOT" archive -o "$WORK/head.tar" HEAD

# One build of HEAD, exported fresh so neither local changes nor build
# leftovers reach it. $1 names the build, $2 the umask the files are extracted
# and built under (so the sources carry its permissions), and $3, when given,
# a date (touch -t) stamped on every exported file.
build() {
    mkdir -p "$WORK/$1/src"
    (
        umask "$2"
        tar -x -f "$WORK/head.tar" -C "$WORK/$1/src"
        if [ -n "${3:-}" ]; then
            find "$WORK/$1/src" -exec touch -t "$3" {} +
        fi
        cd "$WORK/$1/src"
        uv build --quiet --build-constraint build-constraints.txt --require-hashes \
            -o "$WORK/$1/dist"
    )
    set -- "$1" "$WORK/$1/dist"/*.whl "$WORK/$1/dist"/*.tar.gz
    if [ $# -ne 3 ] || [ ! -f "$2" ] || [ ! -f "$3" ]; then
        echo "error: the $1 build did not make exactly one wheel and one sdist:" >&2
        ls -A "$WORK/$1/dist" >&2 || true
        exit 1
    fi
    (cd "$WORK/$1/dist" && sha256 -- * > "$WORK/$1/SHA256SUMS")
}

build first 022
# The second build: other file dates, umask, time zone and locale.
(
    export TZ=Pacific/Auckland LC_ALL=C
    build second 077 200101010000
)

if ! cmp -s "$WORK/first/SHA256SUMS" "$WORK/second/SHA256SUMS"; then
    echo "error: two builds of the same commit differ:" >&2
    diff "$WORK/first/SHA256SUMS" "$WORK/second/SHA256SUMS" >&2 || true
    exit 1
fi
echo "reproducible: two builds of $(git -C "$ROOT" rev-parse --short HEAD) are identical"
cat "$WORK/first/SHA256SUMS"

if [ -n "$AGAINST" ]; then
    # The published sums must name exactly the files rebuilt, and each must
    # match: a sums file covering only the sdist would leave the wheel unchecked.
    names() { awk '{ sub(/^\*/, "", $2); print $2 }' "$1" | sort; }
    if [ "$(names "$AGAINST")" != "$(names "$WORK/first/SHA256SUMS")" ]; then
        echo "error: $AGAINST does not list exactly the rebuilt files:" >&2
        names "$WORK/first/SHA256SUMS" >&2
        exit 1
    fi
    if ! (cd "$WORK/first/dist" && sha256 -c -- "$AGAINST"); then
        echo "error: the rebuild does not match $AGAINST" >&2
        exit 1
    fi
    echo "matches $AGAINST"
fi
