"""Queue behaviour that a party actually depends on: stable addressing while
several phones edit at once, and a fair singer rotation."""

import json

from conftest import track


def titles(state):
    return [t["title"] for t in state["tracks"]]


def playing(state):
    index = state["currentIndex"]
    return state["tracks"][index]["title"] if index >= 0 else None


def test_every_entry_gets_a_uid(queue):
    state = queue.set_queue([track("a"), track("b")], 0)
    uids = [t["uid"] for t in state["tracks"]]
    assert all(uids) and len(set(uids)) == 2


def test_duplicate_songs_get_distinct_uids(queue):
    queue.set_queue([track("a", video_id="same")], 0)
    state = queue.add_track(track("a", video_id="same"))
    assert state["tracks"][0]["uid"] != state["tracks"][1]["uid"]


def test_remove_targets_the_song_not_the_slot(queue):
    """The bug positional edits caused: a client holding a stale queue asks to
    remove 'c', and by then something else has been inserted above it."""
    state = queue.set_queue([track("a"), track("b"), track("c")], 0)
    doomed_uid = state["tracks"][2]["uid"]

    queue.add_track(track("late"), rotate=False)
    queue.advance_relative(1)

    state = queue.remove_track(doomed_uid)
    assert titles(state) == ["a", "b", "late"]
    assert playing(state) == "b"


def test_remove_ignores_an_unknown_uid(queue):
    queue.set_queue([track("a"), track("b")], 0)
    assert titles(queue.remove_track("gone")) == ["a", "b"]


def test_cannot_remove_the_playing_track(queue):
    state = queue.set_queue([track("a"), track("b")], 0)
    state = queue.remove_track(state["tracks"][0]["uid"])
    assert titles(state) == ["a", "b"]


def test_removing_an_earlier_track_keeps_the_same_song_playing(queue):
    state = queue.set_queue([track("a"), track("b"), track("c")], 2)
    state = queue.remove_track(state["tracks"][0]["uid"])
    assert playing(state) == "c"


def test_advance_to_uid(queue):
    state = queue.set_queue([track("a"), track("b"), track("c")], 0)
    state = queue.advance_to(state["tracks"][2]["uid"])
    assert playing(state) == "c"


def test_advance_relative_stops_at_the_ends(queue):
    queue.set_queue([track("a"), track("b")], 0)
    assert playing(queue.advance_relative(-1)) == "a"
    assert playing(queue.advance_relative(1)) == "b"
    assert playing(queue.advance_relative(1)) == "b"


def test_shuffle_keeps_playing_the_same_entry(queue):
    """Two copies of one song in the queue: shuffle has to follow the copy
    that's playing, which only uid can tell apart."""
    state = queue.set_queue(
        [track("dup", video_id="x"), track("filler")] + [track("dup", video_id="x")], 2
    )
    playing_uid = state["tracks"][2]["uid"]

    state = queue.shuffle()
    assert state["tracks"][state["currentIndex"]]["uid"] == playing_uid


# --- singer rotation -------------------------------------------------------


def test_rotation_interleaves_singers(queue):
    """Ann hogs the queue; Bob turns up. Bob's first song jumps ahead of Ann's
    backlog, because Ann is already on the mic and hasn't yielded a turn."""
    queue.set_queue([track("now", added_by="Ann")], 0)
    for title in ("ann1", "ann2", "ann3"):
        queue.add_track(track(title, added_by="Ann"))

    state = queue.add_track(track("bob1", added_by="Bob"))
    assert titles(state) == ["now", "bob1", "ann1", "ann2", "ann3"]

    state = queue.add_track(track("bob2", added_by="Bob"))
    assert titles(state) == ["now", "bob1", "ann1", "bob2", "ann2", "ann3"]


def test_a_new_singer_joins_the_current_round(queue):
    """Cid arrives late and still gets a turn before anyone's second song."""
    queue.set_queue([track("now", added_by="Ann")], 0)
    queue.add_track(track("ann1", added_by="Ann"))
    queue.add_track(track("bob1", added_by="Bob"))

    state = queue.add_track(track("cid1", added_by="Cid"))
    assert titles(state) == ["now", "bob1", "cid1", "ann1"]


def test_rotation_never_reorders_played_or_playing_songs(queue):
    """Already-played songs and the current one stay put -- and Ann's earlier
    round-one song keeps its place ahead of Bob's, since within a round it's
    still first come, first served."""
    queue.set_queue([track("p1", added_by="Ann"), track("p2", added_by="Ann")], 1)
    queue.add_track(track("ann1", added_by="Ann"))
    state = queue.add_track(track("bob1", added_by="Bob"))
    assert titles(state) == ["p1", "p2", "bob1", "ann1"]
    assert playing(state) == "p2"

    # Ann is two turns deep now, so her next song goes to the back.
    state = queue.add_track(track("ann2", added_by="Ann"))
    assert titles(state) == ["p1", "p2", "bob1", "ann1", "ann2"]


def test_host_adds_share_one_rotation_slot(queue):
    """The host is just another singer in the rotation, so a guest's first
    request comes before the host's second."""
    queue.set_queue([track("now")], 0)
    queue.add_track(track("host1"))
    state = queue.add_track(track("guest1", added_by="Ann"))
    assert titles(state) == ["now", "guest1", "host1"]


def test_rotation_off_is_a_plain_append(queue):
    queue.set_rotation(False)
    queue.set_queue([track("now", added_by="Ann")], 0)
    queue.add_track(track("ann1", added_by="Ann"))
    queue.add_track(track("ann2", added_by="Ann"))
    state = queue.add_track(track("bob1", added_by="Bob"))
    assert titles(state) == ["now", "ann1", "ann2", "bob1"]


def test_radio_filler_never_rotates(queue):
    queue.set_queue([track("now", added_by="Ann")], 0)
    queue.add_track(track("ann1", added_by="Ann"))
    state = queue.add_tracks([track("radio1"), track("radio2")])
    assert titles(state) == ["now", "ann1", "radio1", "radio2"]


def test_adding_to_an_empty_queue_starts_it(queue):
    state = queue.add_track(track("only", added_by="Ann"))
    assert playing(state) == "only"


# --- persistence -----------------------------------------------------------


def test_state_survives_a_reload(queue):
    queue.set_queue([track("a"), track("b")], 1)
    queue.set_rotation(False)

    queue._loaded = False
    state = queue.get_state()
    assert titles(state) == ["a", "b"]
    assert playing(state) == "b"
    assert state["rotation"] is False


def test_legacy_state_without_uids_is_backfilled(queue):
    queue.STATE_PATH.write_text(
        json.dumps({"tracks": [{"title": "old", "videoId": "v"}], "currentIndex": 0}),
        encoding="utf-8",
    )
    state = queue.get_state()
    assert state["tracks"][0]["uid"]
    assert state["rotation"] is True


def test_an_out_of_range_saved_index_is_clamped(queue):
    queue.STATE_PATH.write_text(
        json.dumps({"tracks": [{"title": "a", "videoId": "v", "uid": "u1"}], "currentIndex": 7}),
        encoding="utf-8",
    )
    assert queue.get_state()["currentIndex"] == 0
