import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import queue_state  # noqa: E402


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A fresh queue_state backed by a throwaway file. The module keeps its
    state in globals, so each test has to reset them explicitly."""
    monkeypatch.setattr(queue_state, "STATE_PATH", tmp_path / "queue_state.json")
    monkeypatch.setattr(queue_state, "_queue", [])
    monkeypatch.setattr(queue_state, "_index", -1)
    monkeypatch.setattr(queue_state, "_rotation", True)
    monkeypatch.setattr(queue_state, "_loaded", False)
    return queue_state


def track(title, added_by=None, video_id=None):
    entry = {"title": title, "videoId": video_id or f"vid_{title}", "artist": "Someone"}
    if added_by:
        entry["addedBy"] = added_by
    return entry
