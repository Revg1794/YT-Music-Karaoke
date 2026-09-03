"""Local karaoke-style player for a YouTube Music library.

Run with: uvicorn main:app --reload  (from the backend/ directory)
Then open http://localhost:8000 in a browser.
"""

import asyncio
import base64
import io
import secrets
import socket
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ytmusicapi import YTMusic

import queue_state
from lyrics import fetch_lyrics, set_manual_lyrics, set_offset

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
AUTH_FILE = PROJECT_DIR / "browser.json"

# Anything that changes playback or touches someone else's song is host-only,
# so a guest can't skip the singer or wipe the queue from the back of the room.
# The host page picks this up automatically over localhost; a host screen on
# another device (a TV browser, say) types the PIN once. It's griefing
# prevention among people already on your WiFi, not real authentication.
HOST_PIN = f"{secrets.randbelow(10000):04d}"
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Announce the PIN once the server is actually up, so it lands at the
    bottom of the console window rather than scrolling past during import.
    Explicit flush because stdout is block-buffered when it isn't a terminal."""
    for line in (
        "",
        "  Host PIN for this session: " + HOST_PIN,
        "  (only needed to unlock host controls away from this machine)",
        "",
    ):
        print(line, flush=True)
    yield


app = FastAPI(title="YT Music Karaoke", lifespan=lifespan)


def require_host(x_host_pin: str | None = Header(default=None)) -> None:
    if x_host_pin != HOST_PIN:
        raise HTTPException(
            status_code=403,
            detail="Host controls are locked. Enter the host PIN shown in the server window.",
        )


host_only = [Depends(require_host)]


@app.get("/api/host-pin")
def host_pin(request: Request):
    """Hand the PIN to the host page automatically when it's the machine
    running the server. Guests on the LAN get a 403 and stay guests."""
    client = request.client.host if request.client else None
    if client not in LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="Not a local client.")
    return {"pin": HOST_PIN}


_ytmusic: YTMusic | None = None


def get_ytmusic() -> YTMusic:
    global _ytmusic
    if _ytmusic is None:
        if not AUTH_FILE.exists():
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Missing {AUTH_FILE}. Run `ytmusicapi browser` from the project root "
                    "to generate it (see README.md)."
                ),
            )
        _ytmusic = YTMusic(str(AUTH_FILE))
    return _ytmusic


def _track_summary(track: dict) -> dict:
    artists = track.get("artists") or []
    artist_name = ", ".join(a.get("name", "") for a in artists if a.get("name")) or "Unknown Artist"
    duration_sec = track.get("duration_seconds")
    return {
        "videoId": track.get("videoId"),
        "title": track.get("title", "Unknown Title"),
        "artist": artist_name,
        "durationSeconds": duration_sec,
        "thumbnail": (track.get("thumbnails") or [{}])[-1].get("url"),
    }


def _parse_length(length: str | None) -> int | None:
    """get_watch_playlist tracks report duration as an 'M:SS' string instead
    of duration_seconds."""
    if not length:
        return None
    try:
        parts = [int(p) for p in length.split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def _radio_track_summary(track: dict) -> dict:
    artists = track.get("artists") or []
    artist_name = ", ".join(a.get("name", "") for a in artists if a.get("name")) or "Unknown Artist"
    return {
        "videoId": track.get("videoId"),
        "title": track.get("title", "Unknown Title"),
        "artist": artist_name,
        "durationSeconds": _parse_length(track.get("length")),
        "thumbnail": (track.get("thumbnail") or [{}])[-1].get("url"),
    }


@app.get("/api/radio/{video_id}")
async def radio(video_id: str, limit: int = 10):
    """Similar-songs continuation for a track, used to keep music playing
    automatically once the queue runs out. ytmusicapi's radio mode doesn't
    reliably respect `limit`, so we slice the result ourselves."""
    yt = get_ytmusic()
    result = await asyncio.to_thread(yt.get_watch_playlist, videoId=video_id, limit=limit, radio=True)
    tracks = result.get("tracks", [])
    matches = [
        _radio_track_summary(t) for t in tracks if t.get("videoId") and t.get("videoId") != video_id
    ]
    return matches[:limit]


@app.get("/api/library/playlists")
def library_playlists():
    yt = get_ytmusic()
    playlists = yt.get_library_playlists(limit=200)
    return [
        {"playlistId": p.get("playlistId"), "title": p.get("title"), "count": p.get("count")}
        for p in playlists
    ]


@app.get("/api/library/liked")
def library_liked():
    yt = get_ytmusic()
    data = yt.get_liked_songs(limit=500)
    tracks = data.get("tracks", [])
    return [_track_summary(t) for t in tracks if t.get("videoId")]


@app.get("/api/playlist/{playlist_id}")
def playlist_tracks(playlist_id: str):
    yt = get_ytmusic()
    data = yt.get_playlist(playlist_id, limit=500)
    tracks = data.get("tracks", [])
    return {
        "title": data.get("title"),
        "tracks": [_track_summary(t) for t in tracks if t.get("videoId")],
    }


_library_index_cache: list[dict] | None = None
_library_index_lock = asyncio.Lock()


@app.get("/api/library/all")
async def library_all(refresh: bool = False):
    """Flattened, deduped list of every track across all playlists + Liked Songs,
    for client-side search. Fetches playlists in parallel and caches it, so
    songs saved to the library mid-party need `?refresh=1` to show up."""
    global _library_index_cache
    async with _library_index_lock:
        if _library_index_cache is not None and not refresh:
            return _library_index_cache
        _library_index_cache = await _build_library_index()
        return _library_index_cache


async def _build_library_index() -> list[dict]:
    yt = get_ytmusic()
    playlists = await asyncio.to_thread(yt.get_library_playlists, limit=200)
    liked_data = await asyncio.to_thread(yt.get_liked_songs, limit=500)

    seen_video_ids: set[str] = set()
    all_tracks: list[dict] = []

    for track in liked_data.get("tracks", []):
        if track.get("videoId") and track["videoId"] not in seen_video_ids:
            seen_video_ids.add(track["videoId"])
            all_tracks.append({**_track_summary(track), "playlist": "Liked Songs"})

    async def fetch_playlist(pl: dict) -> list[dict]:
        data = await asyncio.to_thread(yt.get_playlist, pl["playlistId"], limit=500)
        return [
            {**_track_summary(t), "playlist": pl.get("title")}
            for t in data.get("tracks", [])
            if t.get("videoId")
        ]

    playlist_results = await asyncio.gather(
        *(fetch_playlist(pl) for pl in playlists if pl.get("playlistId") != "LM"),
        return_exceptions=True,
    )

    for result in playlist_results:
        if isinstance(result, Exception):
            continue
        for track in result:
            if track["videoId"] not in seen_video_ids:
                seen_video_ids.add(track["videoId"])
                all_tracks.append(track)

    return all_tracks


@app.get("/api/search/catalog")
async def search_catalog(q: str = Query(...)):
    """Live search across all of YouTube Music (not just the user's saved
    library) -- lets party guests request a song even if it isn't saved."""
    yt = get_ytmusic()
    results = await asyncio.to_thread(yt.search, q, filter="songs", limit=15)
    return [_track_summary(r) for r in results if r.get("videoId")]


@app.get("/api/lyrics")
async def lyrics(
    artist: str = Query(...),
    title: str = Query(...),
    duration: int | None = Query(default=None),
    video_id: str | None = Query(default=None),
):
    return await fetch_lyrics(artist, title, duration, video_id)


class LyricsOverride(BaseModel):
    video_id: str
    text: str


@app.post("/api/lyrics/override", dependencies=host_only)
async def lyrics_override(body: LyricsOverride):
    return await asyncio.to_thread(set_manual_lyrics, body.video_id, body.text)


class LyricsOffset(BaseModel):
    video_id: str
    offset_sec: float


@app.post("/api/lyrics/offset", dependencies=host_only)
async def lyrics_offset(body: LyricsOffset):
    """Per-track sync correction, nudged from the host page with [ and ]."""
    return await asyncio.to_thread(set_offset, body.video_id, body.offset_sec)


@app.get("/api/queue")
async def get_queue():
    return await asyncio.to_thread(queue_state.get_state)


class SetQueueBody(BaseModel):
    tracks: list[dict]
    startIndex: int = 0


@app.post("/api/queue/set", dependencies=host_only)
async def set_queue(body: SetQueueBody):
    return await asyncio.to_thread(queue_state.set_queue, body.tracks, body.startIndex)


class AddTrackBody(BaseModel):
    track: dict


@app.post("/api/queue/add")
async def add_to_queue(body: AddTrackBody):
    """The one queue mutation guests are allowed. Where the song lands is up to
    the rotation, not the requester."""
    return await asyncio.to_thread(queue_state.add_track, body.track)


class AddManyBody(BaseModel):
    tracks: list[dict]


@app.post("/api/queue/add_many", dependencies=host_only)
async def add_many_to_queue(body: AddManyBody):
    return await asyncio.to_thread(queue_state.add_tracks, body.tracks)


class UidBody(BaseModel):
    uid: str


@app.post("/api/queue/remove", dependencies=host_only)
async def remove_from_queue(body: UidBody):
    return await asyncio.to_thread(queue_state.remove_track, body.uid)


@app.post("/api/queue/advance", dependencies=host_only)
async def advance_queue(body: UidBody):
    return await asyncio.to_thread(queue_state.advance_to, body.uid)


@app.post("/api/queue/next", dependencies=host_only)
async def next_track():
    return await asyncio.to_thread(queue_state.advance_relative, 1)


@app.post("/api/queue/prev", dependencies=host_only)
async def prev_track():
    return await asyncio.to_thread(queue_state.advance_relative, -1)


class RotationBody(BaseModel):
    enabled: bool


@app.post("/api/queue/rotation", dependencies=host_only)
async def set_rotation(body: RotationBody):
    return await asyncio.to_thread(queue_state.set_rotation, body.enabled)


@app.post("/api/queue/shuffle", dependencies=host_only)
async def shuffle_queue():
    return await asyncio.to_thread(queue_state.shuffle)


# A ring buffer rather than a single slot: the host page polls on an interval,
# and with a room full of people tapping at once, one-in-flight-at-a-time meant
# every reaction but the last got silently dropped between polls.
_reactions: deque[dict] = deque(maxlen=50)
_reaction_seq = 0


class ReactionBody(BaseModel):
    emoji: str
    name: str | None = None


@app.post("/api/react")
def post_reaction(body: ReactionBody):
    global _reaction_seq
    _reaction_seq += 1
    entry = {
        "emoji": body.emoji,
        "seq": _reaction_seq,
        "name": (body.name or "").strip() or None,
    }
    _reactions.append(entry)
    return entry


@app.get("/api/react")
def get_reactions(since: int = 0):
    """Everything newer than the caller's last-seen sequence number. A client
    starting fresh passes since=0 and uses latestSeq to skip the backlog."""
    return {
        "reactions": [r for r in _reactions if r["seq"] > since],
        "latestSeq": _reaction_seq,
    }


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@app.get("/api/party-info")
def party_info():
    lan_url = f"http://{_lan_ip()}:8000/remote"

    img = qrcode.make(lan_url, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_png_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {"lan_url": lan_url, "qr_png_base64": qr_png_base64}


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/remote")
def remote():
    return FileResponse(FRONTEND_DIR / "remote.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
