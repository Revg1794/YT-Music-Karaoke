"""Server-authoritative playback queue, shared across all connected clients
(the host page and any guest /remote pages) and persisted to disk so it
survives both page refreshes and server restarts.

Single-process, synchronous module -- callers running in an async context
should wrap mutations in asyncio.to_thread, same pattern as lyrics.py's cache.
"""

import json
import random
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "queue_state.json"

_queue: list[dict] = []
_index: int = -1
_loaded = False


def _load() -> None:
    global _queue, _index, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        _queue = data.get("tracks", [])
        _index = data.get("currentIndex", -1)
    except (FileNotFoundError, json.JSONDecodeError):
        _queue = []
        _index = -1


def _save() -> None:
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"tracks": _queue, "currentIndex": _index}), encoding="utf-8")
    tmp_path.replace(STATE_PATH)


def get_state() -> dict:
    _load()
    return {"tracks": _queue, "currentIndex": _index}


def set_queue(tracks: list[dict], start_index: int) -> dict:
    global _queue, _index
    _load()
    _queue = tracks
    _index = start_index if 0 <= start_index < len(tracks) else (0 if tracks else -1)
    _save()
    return get_state()


def add_track(track: dict) -> dict:
    global _index
    _load()
    _queue.append(track)
    if _index == -1:
        _index = len(_queue) - 1
    _save()
    return get_state()


def add_tracks(tracks: list[dict]) -> dict:
    """Append multiple tracks at once (used by the auto-radio continuation)."""
    global _index
    _load()
    was_empty = _index == -1
    _queue.extend(tracks)
    if was_empty and tracks:
        _index = len(_queue) - len(tracks)
    _save()
    return get_state()


def remove_track(index: int) -> dict:
    global _index
    _load()
    if 0 <= index < len(_queue) and index != _index:
        _queue.pop(index)
        if index < _index:
            _index -= 1
    _save()
    return get_state()


def advance_to(index: int) -> dict:
    global _index
    _load()
    if 0 <= index < len(_queue):
        _index = index
    _save()
    return get_state()


def shuffle() -> dict:
    global _index
    _load()
    playing_video_id = _queue[_index]["videoId"] if 0 <= _index < len(_queue) else None
    random.shuffle(_queue)
    if playing_video_id:
        _index = next((i for i, t in enumerate(_queue) if t["videoId"] == playing_video_id), _index)
    _save()
    return get_state()
