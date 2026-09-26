"""Session health checks and re-login for the YouTube Music account.

The awkward part: an expired session does not fail cleanly. Google keeps
answering 200, it just serves the signed-out experience -- library calls come
back empty and ytmusicapi raises a KeyError parsing a page shape it did not
expect. So "are we still logged in?" has to be asked structurally rather than
by catching an auth error, because there isn't one.

The structural tell: signed-in browse responses are built around
twoColumnBrowseResultsRenderer, signed-out ones around
singleColumnBrowseResultsRenderer.

Runnable directly as a check, which run.bat uses to decide whether to make you
log in again:
    python backend/ytauth.py --check      (exit 0 = good, 1 = needs login)
"""

import json
import sys
import time
from pathlib import Path

import httpx
from ytmusicapi import YTMusic, setup
from ytmusicapi.helpers import get_authorization, sapisid_from_cookie

PROJECT_DIR = Path(__file__).resolve().parent.parent
AUTH_FILE = PROJECT_DIR / "browser.json"

ORIGIN = "https://music.youtube.com"
SIGNED_OUT_MARKER = "singleColumnBrowseResultsRenderer"
SESSION_COOKIE = "__Secure-3PAPISID"

# Statuses check_auth can report.
OK = "ok"
EXPIRED = "expired"
MISSING = "missing"
OFFLINE = "offline"
UNKNOWN = "unknown"

DASH = "—"

# User-facing, so these use real punctuation rather than the source files'
# ASCII double dash.
MESSAGES = {
    OK: "Connected.",
    EXPIRED: (
        "Your YouTube Music session expired. Google signs these out every couple of weeks "
        "{dash} reconnect to carry on."
    ).replace("{dash}", DASH),
    MISSING: "No YouTube Music account connected yet.",
    OFFLINE: "Couldn't reach YouTube Music. Check your internet connection.",
    UNKNOWN: "Couldn't tell whether the account is still connected.",
}


def _result(status: str, account: str | None = None, detail: str | None = None) -> dict:
    return {
        "status": status,
        "ok": status == OK,
        "account": account,
        "message": MESSAGES.get(status, MESSAGES[UNKNOWN]),
        "detail": detail,
    }


def _looks_signed_out(client: YTMusic) -> bool:
    """Ask for the library page and look at which layout came back. Any failure
    here is left to the caller -- we only answer the signed-out question."""
    response = client._send_request("browse", {"browseId": "FEmusic_liked_playlists"})
    return SIGNED_OUT_MARKER in response.get("contents", {})


def check_auth() -> dict:
    """Is the stored session still usable? Never raises."""
    if not AUTH_FILE.exists():
        return _result(MISSING)

    try:
        client = YTMusic(str(AUTH_FILE))
    except Exception as err:
        return _result(UNKNOWN, detail=f"Couldn't load browser.json: {err}")

    try:
        # get_account_info both proves we're signed in and gives us a name to
        # show, so it's the probe of choice when it works.
        info = client.get_account_info()
        name = (info or {}).get("accountName")
        if name:
            return _result(OK, account=name)
    except (httpx.HTTPError, OSError) as err:
        return _result(OFFLINE, detail=str(err))
    except Exception:
        pass  # fall through to the structural check below

    # get_account_info didn't answer cleanly. Distinguish "signed out" from
    # "ytmusicapi tripped over something else" instead of assuming.
    try:
        if _looks_signed_out(client):
            return _result(EXPIRED)
        return _result(OK)
    except (httpx.HTTPError, OSError) as err:
        return _result(OFFLINE, detail=str(err))
    except Exception as err:
        return _result(UNKNOWN, detail=f"{type(err).__name__}: {err}")


def classify_error(err: BaseException) -> str:
    """Why did a library call just fail? Used to turn an opaque parse error
    into an honest 'you need to log in again' for the UI."""
    if isinstance(err, (httpx.HTTPError, OSError)):
        return OFFLINE
    status = check_auth()["status"]
    return status if status != OK else UNKNOWN


def capture_login(timeout_sec: int = 300, on_status=None) -> dict:
    """Open real Chrome, wait for the user to log in, and write browser.json.

    Blocking, and it opens a window -- call it from a worker thread, never the
    event loop. Unlike the old terminal flow this watches for the session
    cookie instead of waiting on an Enter keypress, so it can be driven from
    the app's own UI.
    """
    from playwright.sync_api import sync_playwright

    def say(message: str) -> None:
        if on_status:
            on_status(message)

    say("Opening Chrome...")

    with sync_playwright() as p:
        try:
            # The real installed Chrome, not Playwright's bundled build --
            # YouTube rejects the latter as a deprecated browser because its
            # user-agent says HeadlessChrome.
            browser = p.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as err:
            raise RuntimeError(
                "Couldn't launch Google Chrome. This needs the real Chrome installed -- "
                "get it from https://www.google.com/chrome/ and try again."
            ) from err

        context = browser.new_context()
        page = context.new_page()
        page.goto(ORIGIN)
        say("Log into your Google account in the Chrome window that just opened.")

        deadline = time.monotonic() + timeout_sec
        cookies: list[dict] = []
        while time.monotonic() < deadline:
            cookies = context.cookies(ORIGIN)
            if any(c["name"] == SESSION_COOKIE for c in cookies):
                # Let the rest of the session cookies land before snapshotting.
                time.sleep(2)
                cookies = context.cookies(ORIGIN)
                break
            time.sleep(1)
        else:
            browser.close()
            raise TimeoutError(
                "Timed out waiting for you to finish logging in. Nothing was changed."
            )

        say("Logged in -- saving the session...")
        browser.close()

    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    # ytmusicapi only needs an authorization value present to recognise this as
    # browser auth; it recomputes a fresh SAPISIDHASH on every real request.
    authorization = get_authorization(sapisid_from_cookie(cookie_header) + " " + ORIGIN)
    headers_raw = (
        f"cookie: {cookie_header}\nx-goog-authuser: 0\nauthorization: {authorization}"
    )
    setup(filepath=str(AUTH_FILE), headers_raw=headers_raw)

    result = check_auth()
    if not result["ok"]:
        raise RuntimeError(
            "Saved the session, but it still doesn't look signed in. "
            f"({result['message']})"
        )
    say("Connected.")
    return result


def main() -> int:
    if "--check" in sys.argv:
        result = check_auth()
        if "--json" in sys.argv:
            print(json.dumps(result))
        else:
            who = f" ({result['account']})" if result["account"] else ""
            print(result["message"] + who)
        # Exit codes are for run.bat: 1 means "make them log in again", while 2
        # means we couldn't tell (usually no internet yet) and it should just
        # start the app rather than dragging the user through a pointless login.
        if result["ok"]:
            return 0
        return 1 if result["status"] in (EXPIRED, MISSING) else 2

    capture_login(on_status=print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
