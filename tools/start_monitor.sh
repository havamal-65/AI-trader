#!/usr/bin/env bash
# Launcher for tools/monitor.py. Resolves its own directory so it works no
# matter where it's invoked from. Forwards any extra args to the monitor
# (e.g. --poll-seconds 60).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/monitor.py" "$@"
