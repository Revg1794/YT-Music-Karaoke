#!/usr/bin/env bash
# run.sh - start YT Music Karaoke (Linux/macOS version of run.bat).
# First run creates the Python environment. Every run checks your YouTube Music sign-in (and walks
# you through signing in again if Google expired it), then starts the server and opens the app.
# The server is also reachable from phones on the same Wi-Fi (party mode) on port 8000.
# Close this window (or press Ctrl+C) to stop the server.
set -euo pipefail
cd "$(dirname "$0")"
# Started from a menu shortcut, the terminal would vanish on an error before it could be read.
trap 'rc=$?; if [ "$rc" -ne 0 ] && [ -t 0 ]; then echo; read -rp "Something went wrong (see above). Press Enter to close... " _; fi' EXIT

if ! command -v python3 >/dev/null; then
  echo "Python 3 was not found. Install it (for example: sudo apt install python3 python3-venv) and run this again."
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
echo "Installing/updating dependencies..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r backend/requirements.txt

echo "Checking your YouTube Music connection..."
set +e
.venv/bin/python backend/ytauth.py --check
rc=$?
set -e
if [ "$rc" -eq 1 ]; then          # 1 = not signed in / expired (same meaning as in run.bat)
  echo
  echo "Let's connect your YouTube Music account."
  if ! .venv/bin/python setup_auth.py; then
    echo "Account setup failed or was cancelled."
    exit 1
  fi
fi

echo
echo "Starting server..."
echo "(bound to your LAN too -- see the \"Guests\" link in the app to invite phones on the same WiFi)"
( sleep 2; xdg-open http://localhost:8000 >/dev/null 2>&1 || open http://localhost:8000 >/dev/null 2>&1 || true ) &
exec .venv/bin/python -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
