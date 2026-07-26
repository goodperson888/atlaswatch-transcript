from __future__ import annotations

import hmac
import html
import json
import os
import re
import urllib.request
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from youtube_transcript_api import YouTubeTranscriptApi

app = FastAPI(title="AtlasWatch Transcript Sidecar", version="1.0.0")

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TAG = re.compile(r"<[^>]+>")
TIMESTAMP = re.compile(
    r"(?P<start>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})[.,](?P<sms>\d{3})"
    r"\s+-->\s+"
    r"(?P<end>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})[.,](?P<ems>\d{3})"
)


def _token() -> str:
    return os.environ.get("TRANSCRIPT_SIDECAR_TOKEN", "")


def _authorize(authorization: Optional[str]) -> None:
    expected = _token()
    supplied = authorization or ""
    if len(expected) < 32 or not hmac.compare_digest(supplied, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="unauthorized")


def _clean(value: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", value)).split())[:1000]


def _seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _from_public_transcript(video_id: str, languages: list[str]) -> list[dict[str, Any]]:
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
    return [
        {
            "start": max(0, float(snippet.start)),
            "duration": max(0, float(snippet.duration)),
            "text": _clean(snippet.text),
        }
        for snippet in fetched
        if _clean(snippet.text)
    ]


def _caption_track(info: dict[str, Any], languages: list[str]) -> Optional[dict[str, Any]]:
    tracks = {**(info.get("automatic_captions") or {}), **(info.get("subtitles") or {})}
    candidates = languages + [key for key in tracks if key not in languages and key != "live_chat"]
    for language in candidates:
        formats = tracks.get(language) or []
        for preferred in ("json3", "vtt"):
            match = next((item for item in formats if item.get("ext") == preferred and item.get("url")), None)
            if match:
                return match
    return None


def _parse_json3(payload: bytes) -> list[dict[str, Any]]:
    value = json.loads(payload)
    segments: list[dict[str, Any]] = []
    for event in value.get("events", []):
        text = _clean("".join(segment.get("utf8", "") for segment in event.get("segs", [])))
        if not text:
            continue
        segments.append({
            "start": max(0, float(event.get("tStartMs", 0)) / 1000),
            "duration": max(0, float(event.get("dDurationMs", 0)) / 1000),
            "text": text,
        })
    return segments


def _parse_vtt(payload: bytes) -> list[dict[str, Any]]:
    lines = payload.decode("utf-8", errors="replace").splitlines()
    segments: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = TIMESTAMP.search(lines[index])
        if not match:
            index += 1
            continue
        start = _seconds(match["start"], match["sm"], match["ss"], match["sms"])
        end = _seconds(match["end"], match["em"], match["es"], match["ems"])
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1
        text = _clean(" ".join(text_lines))
        if text and (not segments or segments[-1]["text"] != text):
            segments.append({"start": start, "duration": max(0, end - start), "text": text})
    return segments


def _from_ytdlp(video_id: str, languages: list[str]) -> list[dict[str, Any]]:
    import yt_dlp

    with yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if not isinstance(info, dict):
        return []
    track = _caption_track(info, languages)
    if not track:
        return []
    request = urllib.request.Request(track["url"], headers={"User-Agent": "AtlasWatch-Transcript/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = response.read(5_000_001)
    if len(payload) > 5_000_000:
        raise ValueError("caption_too_large")
    return _parse_json3(payload) if track.get("ext") == "json3" else _parse_vtt(payload)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "configured": len(_token()) >= 32}


@app.get("/transcript")
def transcript(
    videoId: str = Query(min_length=11, max_length=11),
    languages: str = Query(default="zh-CN,zh,en", max_length=100),
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    if not VIDEO_ID.fullmatch(videoId):
        raise HTTPException(status_code=400, detail="invalid_video_id")
    selected = [value.strip() for value in languages.split(",") if re.fullmatch(r"[A-Za-z0-9-]{2,12}", value.strip())][:8]
    if not selected:
        selected = ["zh-CN", "zh", "en"]
    try:
        segments = _from_public_transcript(videoId, selected)
        provider = "youtube-transcript-api"
    except Exception:
        try:
            segments = _from_ytdlp(videoId, selected)
            provider = "yt-dlp"
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"transcript_unavailable:{type(error).__name__}") from error
    return {"status": "live", "provider": provider, "videoId": videoId, "segments": segments[:10_000]}
