#!/bin/sh
set -eu
umask 077

INTERVAL_HOURS="${SYNC_INTERVAL_HOURS:-6}"
case "$INTERVAL_HOURS" in
    '' | *[!0-9]*) echo "SYNC_INTERVAL_HOURS must be a whole number of hours" >&2; exit 1 ;;
esac
# strip leading zeros so "08" is not read as octal
INTERVAL_HOURS=$(echo "$INTERVAL_HOURS" | sed 's/^0*//')
if [ -z "$INTERVAL_HOURS" ]; then
    echo "SYNC_INTERVAL_HOURS must be at least 1" >&2
    exit 1
fi
INTERVAL_SECONDS=$((INTERVAL_HOURS * 3600))

if [ "$(id -u)" = "0" ]; then
    echo "Warning: running as root. The image is meant to run as UID 10001." >&2
fi

case "${RUN_ONCE:-false}" in
    1 | true | yes)
        echo "Immich -> Amazon Photos sync: single run"
        exec python3 /app/immich_sync.py
        ;;
esac

# PID 1 ignores SIGTERM by default: forward it so `docker stop` ends promptly
# instead of waiting for SIGKILL. State writes are atomic, so stopping mid-run is safe.
child=""
stop() {
    echo "Stopping..."
    if [ -n "$child" ]; then
        kill -TERM "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
    fi
    exit 0
}
trap stop TERM INT

echo "Immich -> Amazon Photos sync starting. Interval: every ${INTERVAL_HOURS}h"

while true; do
    echo "=== Sync run: $(date -Iseconds) ==="
    python3 /app/immich_sync.py &
    child=$!
    rc=0
    wait "$child" || rc=$?
    child=""
    if [ "$rc" -ne 0 ]; then
        echo "Sync finished with exit code $rc (will retry next run)"
    fi
    echo "=== Next run in ${INTERVAL_HOURS}h ==="
    sleep "$INTERVAL_SECONDS" &
    child=$!
    wait "$child" || true
    child=""
done
