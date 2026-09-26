"""One-step YouTube Music auth setup.

Opens a real browser window, lets you log into music.youtube.com normally,
then reads the session cookies directly from that browser and writes
browser.json. No DevTools, no copy-pasting headers.

The work lives in backend/ytauth.py so the app can offer the same "reconnect"
flow from its own UI when the session expires later.

Usage: python setup_auth.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from ytauth import capture_login, check_auth  # noqa: E402


def main() -> int:
    current = check_auth()
    if current["ok"]:
        who = f" as {current['account']}" if current["account"] else ""
        print(f"Already connected{who}. Nothing to do.")
        return 0

    print("Opening Chrome -- log into your Google account on YouTube Music.")
    print("This window closes by itself once you're signed in.")
    try:
        result = capture_login(on_status=print)
    except Exception as err:
        print(f"\n{err}")
        return 1

    who = f" as {result['account']}" if result["account"] else ""
    print(f"\nDone -- connected{who}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
