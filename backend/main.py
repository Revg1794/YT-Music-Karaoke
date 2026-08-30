"""Local karaoke-style player for a YouTube Music library.

Run with: uvicorn main:app --reload  (from the backend/ directory)
Then open http://localhost:8000 in a browser.
"""

import asyncio
import base64
import io
import socket
import time
from pathlib import Path

import qrcode
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ytmusicapi import YTMusic

import queue_state
from lyrics import fetch_lyrics, set_manual_lyrics

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
AUTH_FILE = PROJECT_DIR / "browser.json"

app = FastAPI(title="YT Music Karaoke")

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


@app.get("/api/library/all")
async def library_all():
    """Flattened, deduped list of every track across all playlists + Liked Songs,
    for client-side search. Fetches playlists in parallel and caches for the life
    of the server process (restart to pick up library changes)."""
    global _library_index_cache
    if _library_index_cache is not None:
        return _library_index_cache

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

    _library_index_cache = all_tracks
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


@app.post("/api/lyrics/override")
async def lyrics_override(body: LyricsOverride):
    return await asyncio.to_thread(set_manual_lyrics, body.video_id, body.text)


@app.get("/api/queue")
async def get_queue():
    return await asyncio.to_thread(queue_state.get_state)


class SetQueueBody(BaseModel):
    tracks: list[dict]
    startIndex: int = 0


@app.post("/api/queue/set")
async def set_queue(body: SetQueueBody):
    return await asyncio.to_thread(queue_state.set_queue, body.tracks, body.startIndex)


class AddTrackBody(BaseModel):
    track: dict


@app.post("/api/queue/add")
async def add_to_queue(body: AddTrackBody):
    return await asyncio.to_thread(queue_state.add_track, body.track)


class AddManyBody(BaseModel):
    tracks: list[dict]


@app.post("/api/queue/add_many")
async def add_many_to_queue(body: AddManyBody):
    return await asyncio.to_thread(queue_state.add_tracks, body.tracks)


class IndexBody(BaseModel):
    index: int


@app.post("/api/queue/remove")
async def remove_from_queue(body: IndexBody):
    return await asyncio.to_thread(queue_state.remove_track, body.index)


@app.post("/api/queue/advance")
async def advance_queue(body: IndexBody):
    return await asyncio.to_thread(queue_state.advance_to, body.index)


@app.post("/api/queue/shuffle")
async def shuffle_queue():
    return await asyncio.to_thread(queue_state.shuffle)


_last_reaction: dict = {"emoji": None, "ts": 0}


class ReactionBody(BaseModel):
    emoji: str


@app.post("/api/react")
def post_reaction(body: ReactionBody):
    global _last_reaction
    _last_reaction = {"emoji": body.emoji, "ts": time.time()}
    return _last_reaction


@app.get("/api/react")
def get_reaction():
    return _last_reaction


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
