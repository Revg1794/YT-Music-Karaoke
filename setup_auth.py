"""One-step YouTube Music auth setup.

Opens a real browser window, lets you log into music.youtube.com normally,
then reads the session cookies directly from that browser and writes
browser.json. No DevTools, no copy-pasting headers.

This works because ytmusicapi computes the 'authorization' header itself at
request time from the cookie (see ytmusicapi/ytmusic.py's get_authorization
call), and fills in the rest of the static headers (accept, user-agent, etc.)
automatically -- the only thing that actually has to come from a real,
logged-in browser session is the Cookie header itself.

Usage: python setup_auth.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from ytmusicapi import setup
from ytmusicapi.helpers import get_authorization, sapisid_from_cookie

OUT_PATH = Path(__file__).parent / "browser.json"


def main() -> None:
    print("Opening a browser window -- log into your Google account on YouTube Music.")
    print("Once you're logged in and can see your library, come back here and press Enter.")

    with sync_playwright() as p:
        try:
            # Use the real, installed Chrome (not Playwright's bundled test build) --
            # YouTube flags Playwright's default Chromium as a "deprecated browser"
            # because its user-agent literally says HeadlessChrome.
            browser = p.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            print(
                "\nCouldn't find Google Chrome installed on this PC. This script drives your "
                "real Chrome browser to log in -- please install it from "
                "https://www.google.com/chrome/ and try again."
            )
            sys.exit(1)

        context = browser.new_context()
        page = context.new_page()
        page.goto("https://music.youtube.com")

        input("\nPress Enter here once you're logged in... ")

        cookies = context.cookies("https://music.youtube.com")
        browser.close()

    cookie_names = {c["name"] for c in cookies}
    if "__Secure-3PAPISID" not in cookie_names:
        print(
            "\nDidn't find a logged-in session cookie (__Secure-3PAPISID missing). "
            "Make sure you actually finished logging in before pressing Enter, then try again."
        )
        sys.exit(1)

    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    # ytmusicapi needs a SAPISIDHASH authorization value present just to recognize this as
    # browser auth (it recomputes a fresh one on every real request afterwards regardless --
    # see ytmusic.py's `headers` property), so compute one the same way it does internally.
    origin = "https://music.youtube.com"
    sapisid = sapisid_from_cookie(cookie_header)
    authorization = get_authorization(sapisid + " " + origin)

    headers_raw = f"cookie: {cookie_header}\nx-goog-authuser: 0\nauthorization: {authorization}"

    setup(filepath=str(OUT_PATH), headers_raw=headers_raw)
    print(f"\nDone. Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
