#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 mock_game.py &
MOCK_PID=$!
trap 'kill "$MOCK_PID" 2>/dev/null || true' EXIT INT TERM
sleep 1
python3 app.py
