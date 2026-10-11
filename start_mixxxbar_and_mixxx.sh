#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PORT_NAME='BUSYBAR Mixxx Status'
MIXXX_BIN=${MIXXX_BIN:-/usr/bin/mixxx}
PORT_WAIT_SECONDS=${BUSYBAR_PORT_WAIT_SECONDS:-10}
bridge_pid=

case "$PORT_WAIT_SECONDS" in
    ''|*[!0-9]*)
        printf '%s\n' 'Invalid BUSYBAR_PORT_WAIT_SECONDS; using 10.' >&2
        PORT_WAIT_SECONDS=10
        ;;
esac
if [ "${#PORT_WAIT_SECONDS}" -gt 2 ] || [ "$PORT_WAIT_SECONDS" -gt 60 ]; then
    PORT_WAIT_SECONDS=60
fi

start_mixxx() {
    /usr/bin/env QT_QPA_PLATFORM=xcb "$MIXXX_BIN"
}

cleanup() {
    if [ -n "$bridge_pid" ]; then
        kill "$bridge_pid" 2>/dev/null || true
        wait "$bridge_pid" 2>/dev/null || true
        bridge_pid=
    fi
}

if ! command -v aconnect >/dev/null 2>&1; then
    printf '%s\n' 'aconnect is unavailable; starting Mixxx without BUSY Bar integration.' >&2
    start_mixxx
    exit $?
fi

if [ ! -r "$SCRIPT_DIR/busybar.env" ] && [ -z "${BUSY_API_TOKEN:-}" ]; then
    printf '%s\n' 'BUSY_API_TOKEN is unavailable; starting Mixxx without BUSY Bar integration.' >&2
    start_mixxx
    exit $?
fi

(
    if [ -r "$SCRIPT_DIR/busybar.env" ]; then
        set -a
        # The local secret file is intentionally unavailable to static analysis.
        # shellcheck disable=SC1091
        . "$SCRIPT_DIR/busybar.env"
        set +a
    fi
    exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/start_mixxxbar.py" "$@"
) &
bridge_pid=$!
unset BUSY_API_TOKEN
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

port_ready=0
waited=0
while :; do
    if aconnect -l 2>/dev/null | grep -Fq "$PORT_NAME"; then
        port_ready=1
        break
    fi
    if ! kill -0 "$bridge_pid" 2>/dev/null; then
        wait "$bridge_pid" 2>/dev/null || true
        bridge_pid=
        break
    fi
    if [ "$waited" -ge "$PORT_WAIT_SECONDS" ]; then
        break
    fi
    waited=$((waited + 1))
    sleep 1
done

if [ "$port_ready" -eq 0 ]; then
    printf '%s\n' 'BUSY Bar MIDI port is unavailable; starting Mixxx without BUSY Bar integration.' >&2
    cleanup
fi

start_mixxx
