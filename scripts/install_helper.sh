#!/bin/bash
# Install or remove the JobRadar helper as a login agent.
#
#   scripts/install_helper.sh            install and start
#   scripts/install_helper.sh --uninstall  stop and remove
#
# The helper used to exit after 30 idle minutes, which meant a dead page at the
# moment you sat down to work. The Origin, Host and Content-Type checks are what
# actually guard it; the timeout was defence in depth that cost more than it
# saved. It stays up now, at 19MB and 0% CPU, bound to loopback.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.alli.jobradar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/.jobradar/logs"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $PLIST"
  echo "the helper is stopped. Run it by hand any time with:"
  echo "  $REPO/.venv/bin/python -m jobradar serve"
  exit 0
fi

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no interpreter at $PY; create the venv first" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
sed -e "s|__PYTHON__|$PY|" -e "s|__REPO__|$REPO|" -e "s|__LOG__|$LOGDIR|" \
    "$REPO/scripts/$LABEL.plist" > "$PLIST"

# bootout first so a re-run replaces cleanly rather than erroring on a
# already-loaded label.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

sleep 2
if curl -sf -m 5 -H "Host: 127.0.0.1:8777" http://127.0.0.1:8777/api/health >/dev/null; then
  echo "installed and running: http://localhost:8777"
  echo "  plist  $PLIST"
  echo "  log    $LOGDIR/jobradar-helper.log"
  echo "  remove scripts/install_helper.sh --uninstall"
else
  echo "installed but not answering yet; check $LOGDIR/jobradar-helper.log" >&2
  exit 1
fi
