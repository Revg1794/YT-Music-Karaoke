"""Session-health detection.

The case that matters: an expired session doesn't raise an auth error. Google
answers 200 with the signed-out page shape, so these tests pin the structural
check rather than any exception type.
"""

import httpx
import pytest

import ytauth


class FakeClient:
    """Stands in for YTMusic. `account` None means get_account_info fails the
    way it really does when signed out -- a KeyError from parsing a page that
    has no account header on it."""

    def __init__(self, account=None, signed_out=False, raises=None):
        self._account = account
        self._signed_out = signed_out
        self._raises = raises

    def get_account_info(self):
        if self._raises:
            raise self._raises
        if self._account is None:
            raise KeyError("Unable to find 'header' using path [...]")
        return {"accountName": self._account}

    def _send_request(self, endpoint, body):
        if self._raises:
            raise self._raises
        key = (
            "singleColumnBrowseResultsRenderer"
            if self._signed_out
            else "twoColumnBrowseResultsRenderer"
        )
        return {"contents": {key: {}}}


@pytest.fixture
def auth(tmp_path, monkeypatch):
    """Point the module at a throwaway auth file that exists by default."""
    path = tmp_path / "browser.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ytauth, "AUTH_FILE", path)
    return ytauth


def use_client(monkeypatch, client):
    monkeypatch.setattr(ytauth, "YTMusic", lambda *a, **kw: client)


def test_missing_auth_file(auth, tmp_path, monkeypatch):
    monkeypatch.setattr(ytauth, "AUTH_FILE", tmp_path / "nope.json")
    result = auth.check_auth()
    assert result["status"] == ytauth.MISSING
    assert result["ok"] is False


def test_signed_in_reports_the_account_name(auth, monkeypatch):
    use_client(monkeypatch, FakeClient(account="Mike"))
    result = auth.check_auth()
    assert result["status"] == ytauth.OK
    assert result["ok"] is True
    assert result["account"] == "Mike"


def test_expired_session_is_detected_without_an_auth_error(auth, monkeypatch):
    """The real-world failure: no exception from the server, just the
    signed-out layout and a KeyError out of ytmusicapi."""
    use_client(monkeypatch, FakeClient(account=None, signed_out=True))
    result = auth.check_auth()
    assert result["status"] == ytauth.EXPIRED
    assert "expired" in result["message"].lower()


def test_a_parse_failure_while_still_signed_in_is_not_called_expired(auth, monkeypatch):
    """get_account_info can break for reasons other than being logged out.
    Telling someone to log in again when they're already logged in is worse
    than admitting we don't know."""
    use_client(monkeypatch, FakeClient(account=None, signed_out=False))
    assert auth.check_auth()["status"] == ytauth.OK


def test_network_failure_is_not_mistaken_for_expiry(auth, monkeypatch):
    use_client(monkeypatch, FakeClient(raises=httpx.ConnectError("no route")))
    result = auth.check_auth()
    assert result["status"] == ytauth.OFFLINE
    assert result["ok"] is False


def test_unreadable_auth_file(auth, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("not json")

    monkeypatch.setattr(ytauth, "YTMusic", boom)
    assert auth.check_auth()["status"] == ytauth.UNKNOWN


def test_classify_error_prefers_the_network_explanation(auth, monkeypatch):
    called = []
    monkeypatch.setattr(ytauth, "check_auth", lambda: called.append(1) or {"status": "ok"})
    assert ytauth.classify_error(httpx.ConnectError("down")) == ytauth.OFFLINE
    assert not called, "a network error shouldn't cost a session probe"


def test_classify_error_asks_the_session_for_other_failures(auth, monkeypatch):
    monkeypatch.setattr(ytauth, "check_auth", lambda: {"status": ytauth.EXPIRED})
    assert ytauth.classify_error(KeyError("weird page")) == ytauth.EXPIRED


def test_classify_error_when_the_session_is_fine(auth, monkeypatch):
    monkeypatch.setattr(ytauth, "check_auth", lambda: {"status": ytauth.OK})
    assert ytauth.classify_error(KeyError("weird page")) == ytauth.UNKNOWN
