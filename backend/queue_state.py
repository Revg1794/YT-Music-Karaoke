"""Server-authoritative playback queue, shared across all connected clients
(the host page and any guest /remote pages) and persisted to disk so it
survives both page refreshes and server restarts.

Every entry carries a `uid` assigned on insertion. Clients address tracks by
uid rather than by position, because a guest phone only re-syncs the queue
every few seconds -- with positional edits, two people acting at once would
delete or jump to whatever song happened to slide into that slot.

Single-process, synchronous module -- callers running in an async context
should wrap mutations in asyncio.to_thread, same pattern as lyrics.py's cache.
"""

import json
import random
import uuid
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "queue_state.json"

_queue: list[dict] = []
_index: int = -1
_rotation: bool = True
_loaded = False


def _new_uid() -> str:
    return uuid.uuid4().hex[:12]


def _singer(track: dict) -> str:
    """Whose turn a queue entry belongs to. Host-added songs share one slot in
    the rotation rather than each counting as a separate singer."""
    return (track.get("addedBy") or "").strip() or "Host"


def _load() -> None:
    global _queue, _index, _loaded, _rotation
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        _queue = data.get("tracks", [])
        _index = data.get("currentIndex", -1)
        _rotation = bool(data.get("rotation", True))
    except (FileNotFoundError, json.JSONDecodeError):
        _queue = []
        _index = -1
        _rotation = True

    # Entries saved before uids existed, and any out-of-range index from a
    # partially written state file, would otherwise break every lookup below.
    for track in _queue:
        if not track.get("uid"):
            track["uid"] = _new_uid()
    if not -1 <= _index < len(_queue):
        _index = 0 if _queue else -1


def _save() -> None:
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps({"tracks": _queue, "currentIndex": _index, "rotation": _rotation}),
        encoding="utf-8",
    )
    tmp_path.replace(STATE_PATH)


def _position_of(uid: str) -> int | None:
    for i, track in enumerate(_queue):
        if track.get("uid") == uid:
            return i
    return None


def get_state() -> dict:
    _load()
    return {"tracks": _queue, "currentIndex": _index, "rotation": _rotation}


def set_rotation(enabled: bool) -> dict:
    global _rotation
    _load()
    _rotation = bool(enabled)
    _save()
    return get_state()


def _rotation_insert_pos(singer: str) -> int:
    """Where a new song by `singer` goes under round-robin fairness: after
    everyone else's song of the same round, before anyone's later round. So a
    guest who queues five songs in a row doesn't lock out the next person --
    their 2nd song waits until everyone else has had a 1st.

    Only the current song and what's still pending count. Whoever is on the mic
    right now has just had their turn, so their next request sits behind anyone
    who hasn't sung yet; anything already played is water under the bridge."""
    start = _index + 1
    pending = _queue[start:]

    seen: dict[str, int] = {}
    if 0 <= _index < len(_queue):
        seen[_singer(_queue[_index])] = 1

    rounds: list[int] = []
    for track in pending:
        name = _singer(track)
        seen[name] = seen.get(name, 0) + 1
        rounds.append(seen[name])

    new_round = seen.get(singer, 0) + 1
    for offset, round_no in enumerate(rounds):
        if round_no > new_round:
            return start + offset
    return len(_queue)


def set_queue(tracks: list[dict], start_index: int) -> dict:
    global _queue, _index
    _load()
    _queue = [{**track, "uid": _new_uid()} for track in tracks]
    _index = start_index if 0 <= start_index < len(_queue) else (0 if _queue else -1)
    _save()
    return get_state()


def add_track(track: dict, rotate: bool | None = None) -> dict:
    """Append a track, or slot it into the singer rotation when that's on.
    `rotate` overrides the persisted setting (used by callers that always want
    a plain append)."""
    global _index
    _load()
    entry = {**track, "uid": _new_uid()}

    use_rotation = _rotation if rotate is None else rotate
    pos = _rotation_insert_pos(_singer(entry)) if use_rotation and _queue else len(_queue)
    _queue.insert(pos, entry)

    if _index == -1:
        _index = pos
    elif pos <= _index:
        _index += 1

    _save()
    return get_state()


def add_tracks(tracks: list[dict]) -> dict:
    """Append multiple tracks at once (used by the auto-radio continuation).
    Always a plain append -- filler songs belong to nobody's turn."""
    global _index
    _load()
    was_empty = _index == -1
    _queue.extend({**track, "uid": _new_uid()} for track in tracks)
    if was_empty and tracks:
        _index = len(_queue) - len(tracks)
    _save()
    return get_state()


def remove_track(uid: str) -> dict:
    global _index
    _load()
    pos = _position_of(uid)
    if pos is not None and pos != _index:
        _queue.pop(pos)
        if pos < _index:
            _index -= 1
        _save()
    return get_state()


def advance_to(uid: str) -> dict:
    global _index
    _load()
    pos = _position_of(uid)
    if pos is not None:
        _index = pos
        _save()
    return get_state()


def advance_relative(delta: int) -> dict:
    """Step to the next/previous entry. Resolved server-side so a stale client
    can't jump to the wrong song after the queue shifted underneath it."""
    global _index
    _load()
    target = _index + delta
    if 0 <= target < len(_queue):
        _index = target
        _save()
    return get_state()


def shuffle() -> dict:
    global _index
    _load()
    playing_uid = _queue[_index].get("uid") if 0 <= _index < len(_queue) else None
    random.shuffle(_queue)
    if playing_uid:
        _index = next(
            (i for i, t in enumerate(_queue) if t.get("uid") == playing_uid), _index
        )
    _save()
    return get_state()
