from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

from lyrik.config import cache_dir

LRCLIB_GET = "https://lrclib.net/api/get"
LRCLIB_SEARCH = "https://lrclib.net/api/search"
USER_AGENT = "Lyrik/0.1 (Linux Spotify overlay; lyrics via lrclib.net)"
TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


@dataclass(frozen=True)
class LyricLine:
    time: float
    text: str


@dataclass
class Lyrics:
    source: str = ""
    synced: list[LyricLine] = field(default_factory=list)
    plain: str = ""
    instrumental: bool = False

    def line_index_at(self, position: float) -> int:
        if not self.synced:
            return -1
        idx = -1
        for i, line in enumerate(self.synced):
            if line.time <= position + 0.05:
                idx = i
            else:
                break
        return idx

    def is_empty(self) -> bool:
        return not self.synced and not self.plain.strip() and not self.instrumental


def parse_lrc(raw: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    if not raw:
        return lines
    for original in raw.splitlines():
        stamps = list(TIMESTAMP_RE.finditer(original))
        if not stamps:
            continue
        text = TIMESTAMP_RE.sub("", original).strip()
        if not text or text == "♪":
            continue
        for match in stamps:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            lines.append(LyricLine(time=minutes * 60 + seconds, text=text))
    lines.sort(key=lambda item: item.time)
    return lines


def _cache_key(artist: str, title: str, album: str, duration: int | None) -> str:
    payload = f"{artist.lower()}|{title.lower()}|{album.lower()}|{duration or 0}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _read_cache(key: str) -> Lyrics | None:
    path = cache_dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        synced = [LyricLine(time=float(item["time"]), text=str(item["text"])) for item in data.get("synced", [])]
        return Lyrics(
            source=data.get("source", "cache"),
            synced=synced,
            plain=data.get("plain", ""),
            instrumental=bool(data.get("instrumental", False)),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_cache(key: str, lyrics: Lyrics) -> None:
    path = cache_dir() / f"{key}.json"
    payload = {
        "source": lyrics.source,
        "plain": lyrics.plain,
        "instrumental": lyrics.instrumental,
        "synced": [asdict(line) for line in lyrics.synced],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _http_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _lyrics_from_payload(payload: dict[str, Any], source: str) -> Lyrics:
    if payload.get("instrumental"):
        return Lyrics(source=source, instrumental=True)
    synced = parse_lrc(payload.get("syncedLyrics") or "")
    plain = (payload.get("plainLyrics") or "").strip()
    return Lyrics(source=source, synced=synced, plain=plain)


def fetch_lyrics(artist: str, title: str, album: str = "", duration: int | None = None) -> Lyrics:
    artist = artist.strip()
    title = title.strip()
    album = album.strip()
    if not title:
        return Lyrics()

    key = _cache_key(artist, title, album, duration)
    cached = _read_cache(key)
    if cached is not None:
        return cached

    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = str(duration)

    lyrics = Lyrics()
    try:
        payload = _http_json(f"{LRCLIB_GET}?{urllib.parse.urlencode(params)}")
        if isinstance(payload, dict):
            lyrics = _lyrics_from_payload(payload, "lrclib")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    except urllib.error.URLError:
        search = urllib.parse.urlencode({"q": f"{artist} {title}".strip()})
        try:
            results = _http_json(f"{LRCLIB_SEARCH}?{search}")
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return Lyrics()
        else:
            if isinstance(results, list) and results:
                lyrics = _lyrics_from_payload(results[0], "lrclib-search")
            return lyrics

    if lyrics.is_empty():
        search = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        try:
            results = _http_json(f"{LRCLIB_SEARCH}?{search}")
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            results = []
        if isinstance(results, list) and results:
            lyrics = _lyrics_from_payload(results[0], "lrclib-search")

    if not lyrics.is_empty() or lyrics.instrumental:
        _write_cache(key, lyrics)
    return lyrics
