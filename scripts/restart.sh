#!/bin/bash
# Drain-aware daemon restart: the only restart path (a bare kickstart killed a fable wake
# mid-run, 2026-08-12). Waits for inflight.json to empty, then kickstart -k, then confirms
# the fresh start line landed in the log.
set -euo pipefail

launchd_label() {
    printf '%s\n' "${AGENT_TEAM_LAUNCHD_LABEL:-com.agent-team}"
}

if [ "${1:-}" = "--selftest" ]; then
    test "$(unset AGENT_TEAM_LAUNCHD_LABEL; launchd_label)" = "com.agent-team"
    test "$(AGENT_TEAM_LAUNCHD_LABEL=com.example.agent-team launchd_label)" = "com.example.agent-team"
    echo "restart.sh selftest: 2 PASS, 0 FAIL"
    exit 0
fi

TIMEOUT_SEC="${1:-2100}"   # ~35 min default
POLL_SEC=15
CONFIRM_TIMEOUT_SEC=30

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${AGENT_TEAM_STATE_DIR:-${AGENT_TEAM_CONFIG_DIR:-$HOME/.config/agent-team}/state}"
LOGS_DIR="${AGENT_TEAM_LOGS_DIR:-$HOME/.config/agent-team/logs}"
LOG_PATH="$LOGS_DIR/listener.err.log"  # launchd routes the logger's stderr here
LABEL="$(launchd_label)"

inflight_count() {
    PYTHONPATH="$SCRIPT_DIR" python3 -c "import store; print(len(store.inflight_all()))"
}

echo "restart.sh: waiting for inflight to drain (timeout ${TIMEOUT_SEC}s, poll ${POLL_SEC}s)"
elapsed=0
while true; do
    n="$(inflight_count)"
    if [ "$n" -eq 0 ]; then
        echo "restart.sh: drained, 0 inflight after ${elapsed}s"
        find "$STATE_DIR" -name 'lane-*.lock' -delete
        break
    fi
    if [ "$elapsed" -ge "$TIMEOUT_SEC" ]; then
        echo "restart.sh: TIMEOUT after ${TIMEOUT_SEC}s with $n still inflight; proceeding anyway"
        break
    fi
    sleep "$POLL_SEC"
    elapsed=$((elapsed + POLL_SEC))
done

# The listener imports from the main checkout, so pull that one, never the worktree this script
# may be sitting in: a merge pressed on GitHub moves the remote only, and a restart on the old
# code looks exactly like the fix not working.
MAIN_REPO="$(cd "$(git -C "$SCRIPT_DIR" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
echo "restart.sh: git pull --ff-only in $MAIN_REPO"
git -C "$MAIN_REPO" pull --ff-only || echo "restart.sh: WARNING pull failed; restarting on the checkout as it stands"
echo "restart.sh: HEAD is $(git -C "$MAIN_REPO" rev-parse --short HEAD)"

before_lines=0
if [ -f "$LOG_PATH" ]; then
    before_lines="$(wc -l < "$LOG_PATH")"
fi

echo "restart.sh: launchctl kickstart -k gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "restart.sh: confirming fresh start line in $LOG_PATH"
confirm_elapsed=0
while true; do
    if [ -f "$LOG_PATH" ] && tail -n "+$((before_lines + 1))" "$LOG_PATH" | grep -q "agent-team listener starting"; then
        echo "restart.sh: confirmed fresh start"
        exit 0
    fi
    if [ "$confirm_elapsed" -ge "$CONFIRM_TIMEOUT_SEC" ]; then
        echo "restart.sh: WARNING no fresh start line seen within ${CONFIRM_TIMEOUT_SEC}s; check $LOG_PATH by hand"
        exit 1
    fi
    sleep 2
    confirm_elapsed=$((confirm_elapsed + 2))
done
