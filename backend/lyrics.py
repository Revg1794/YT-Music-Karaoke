"""LRCLIB client + .lrc synced-lyrics parser, with a YouTube captions fallback.

LRCLIB is crowd-sourced and frequently has multiple, sometimes mislabeled,
submissions for the same artist/title/duration (e.g. a rip with the wrong
lyrics attached). Rather than trusting LRCLIB's own /get "exact match" --
which has been observed to return different entries for what looks like the
same query -- we always search and rank candidates ourselves.

When LRCLIB has no match at all, we fall back to the video's own YouTube
captions (creator-uploaded or auto-generated) as a synced-lyrics source --
they're already time-aligned with the audio, just not always as clean as a
proper lyrics transcription.
"""

import asyncio
import json
import re
from pathlib import Path

import httpx
from youtube_transcript_api import YouTubeTranscriptApi

LRCLIB_BASE = "https://lrclib.net/api"
CACHE_PATH = Path(__file__).resolve().parent.parent / "lyrics_cache.json"

_cache: dict[str, dict] | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _save_cache() -> None:
    tmp_path = CACHE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(CACHE_PATH)

_LRC_TIME_RE = re.compile(r"\[(\d{2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WORD_TIME_RE = re.compile(r"<(\d{2}):(\d{2})(?:[.:](\d{1,3}))?>")


def _parse_words(text: str) -> list[dict] | None:
    """Parse enhanced-LRC inline word timestamps (<mm:ss.xx>word) if present.
    Rare in practice (most LRC sources are line-level only), but some
    submissions -- and hand-timed manual overrides -- include them, enabling
    word-by-word "bouncing ball" highlighting instead of whole-line."""
    matches = list(_WORD_TIME_RE.finditer(text))
    if not matches:
        return None

    words = []
    for i, match in enumerate(matches):
        minutes, seconds, frac = match.groups()
        frac_sec = float(f"0.{frac}") if frac else 0.0
        time_sec = int(minutes) * 60 + int(seconds) + frac_sec
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        word_text = text[start:end].strip()
        if word_text:
            words.append({"time_sec": round(time_sec, 3), "text": word_text})
    return words or None


def parse_lrc(lrc_text: str) -> list[dict]:
    """Parse LRC-format synced lyrics into a list of {time_sec, text[, words]},
    sorted by time. `words` is only present when the source included inline
    word-level timestamps."""
    lines = []
    for raw_line in lrc_text.splitlines():
        matches = list(_LRC_TIME_RE.finditer(raw_line))
        if not matches:
            continue
        rest = _LRC_TIME_RE.sub("", raw_line).strip()
        words = _parse_words(rest)
        display_text = _WORD_TIME_RE.sub("", rest).strip() if words else rest
        for match in matches:
            minutes, seconds, frac = match.groups()
            frac_sec = float(f"0.{frac}") if frac else 0.0
            time_sec = int(minutes) * 60 + int(seconds) + frac_sec
            entry = {"time_sec": round(time_sec, 3), "text": display_text}
            if words:
                entry["words"] = words
            lines.append(entry)
    lines.sort(key=lambda item: item["time_sec"])
    return lines


def _rank_key(candidate: dict, title: str, duration: int | None) -> tuple:
    """Lower is better. Prefers an exact title match, then closest duration,
    then a candidate that has a real album tag (a rough signal of a
    legitimate, non-duplicate submission over an untitled/auto-ripped one)."""
    cand_title = (candidate.get("trackName") or "").strip().lower()
    title_exact = cand_title == title.strip().lower()

    cand_duration = candidate.get("duration")
    if duration and cand_duration is not None:
        duration_diff = abs(cand_duration - duration)
    else:
        duration_diff = 999.0

    has_album = bool(candidate.get("albumName"))

    return (0 if title_exact else 1, round(duration_diff), 0 if has_album else 1)


def _lines_from_lyrics(synced: str | None, plain: str | None) -> dict | None:
    if synced:
        return {"synced": True, "lines": parse_lrc(synced), "source": "lrclib"}
    if plain:
        return {
            "synced": False,
            "lines": [{"time_sec": None, "text": line} for line in plain.splitlines() if line.strip()],
            "source": "lrclib",
        }
    return None


def _fetch_captions_sync(video_id: str) -> dict | None:
    """Blocking call -- run via asyncio.to_thread. Returns None on any failure
    (captions disabled, none available, video unavailable, etc.)."""
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id)
    except Exception:
        return None

    lines = [
        {"time_sec": round(snippet.start, 3), "text": snippet.text.replace("\n", " ").strip()}
        for snippet in transcript
        if snippet.text.strip()
    ]
    if not lines:
        return None

    return {
        "synced": True,
        "lines": lines,
        "source": "captions",
        "autoGenerated": transcript.is_generated,
    }


def set_manual_lyrics(video_id: str, text: str) -> dict:
    """Store a user-supplied lyrics override for a track, keyed by video_id in
    the same cache fetch_lyrics reads from -- so it takes priority over
    LRCLIB/captions on every future lookup. Blocking (file I/O); call via
    asyncio.to_thread from an async context.

    An empty/whitespace-only text clears the override (and any previously
    cached auto-lookup result) so the next fetch does a fresh lookup."""
    cache = _load_cache()
    text = text.strip()

    if not text:
        cache.pop(video_id, None)
        _save_cache()
        return {"synced": False, "lines": [], "source": None}

    if _LRC_TIME_RE.search(text):
        result = {"synced": True, "lines": parse_lrc(text), "source": "manual"}
    else:
        result = {
            "synced": False,
            "lines": [{"time_sec": None, "text": line} for line in text.splitlines() if line.strip()],
            "source": "manual",
        }

    cache[video_id] = result
    _save_cache()
    return result


async def fetch_lyrics(artist: str, title: str, duration: int | None, video_id: str | None = None) -> dict:
    """Look up synced lyrics, using a local on-disk cache keyed by video_id so
    repeat plays of the same track skip LRCLIB/YouTube entirely."""
    cache = _load_cache()
    if video_id and video_id in cache:
        return cache[video_id]

    result = await _fetch_lyrics_uncached(artist, title, duration, video_id)

    if video_id:
        cache[video_id] = result
        await asyncio.to_thread(_save_cache)

    return result


async def _fetch_lyrics_uncached(artist: str, title: str, duration: int | None, video_id: str | None) -> dict:
    """Look up synced lyrics on LRCLIB, falling back to YouTube captions if
    LRCLIB has no match. Returns {synced, lines, source}. Never raises on a miss."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{LRCLIB_BASE}/search", params={"artist_name": artist, "track_name": title})
        except httpx.HTTPError:
            resp = None

        if resp is not None and resp.status_code == 200:
            results = resp.json()
            if results:
                results.sort(key=lambda c: _rank_key(c, title, duration))

                for candidate in results:
                    result = _lines_from_lyrics(candidate.get("syncedLyrics"), None)
                    if result:
                        return result

                for candidate in results:
                    result = _lines_from_lyrics(None, candidate.get("plainLyrics"))
                    if result:
                        return result

    if video_id:
        captions_result = await asyncio.to_thread(_fetch_captions_sync, video_id)
        if captions_result:
            return captions_result

    return {"synced": False, "lines": [], "source": None}
