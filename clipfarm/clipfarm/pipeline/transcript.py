"""Transcript acquisition.

Order of preference:
1. YouTube's own captions via youtube-transcript-api (instant, no GPU).
2. Local speech-to-text with faster-whisper if installed (works on any video).
3. None — analysis falls back to audio-energy-only scoring.

The rest of the pipeline consumes a flat list of `Segment`s.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger(__name__)

_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


@dataclass
class Segment:
    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict:
        return asdict(self)


def extract_video_id(url: str) -> str | None:
    m = _YT_ID_RE.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    return None


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    return text


def fetch_youtube_transcript(url: str, languages: tuple[str, ...] = ("en", "en-US", "en-GB")) -> list[Segment] | None:
    """Fetch captions from YouTube. Returns None when unavailable."""
    video_id = extract_video_id(url)
    if not video_id:
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id, languages=list(languages))
        except Exception:
            # Fall back to any language (auto-generated included)
            listing = api.list(video_id)
            transcript = next(iter(listing))
            fetched = transcript.fetch()
        segments = []
        for s in fetched:
            text = _clean(s.text)
            if not text:
                continue
            # Drop bracket annotations except laughter/applause, which the
            # virality scorer uses as reaction signals.
            if text.startswith("[") and text.lower() not in ("[laughter]", "[applause]"):
                continue
            segments.append(Segment(start=float(s.start), duration=float(s.duration), text=text))
        return segments or None
    except Exception as exc:  # noqa: BLE001 — any failure means "no captions"
        log.info("YouTube transcript unavailable: %s", exc)
        return None


def transcribe_with_whisper(video_path: Path) -> list[Segment] | None:
    """Local STT fallback. Only used when faster-whisper is installed."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.info("faster-whisper not installed; skipping local transcription")
        return None
    try:
        model = WhisperModel("base", compute_type="int8")
        raw_segments, _info = model.transcribe(str(video_path), vad_filter=True)
        segments = [
            Segment(start=float(s.start), duration=float(s.end - s.start), text=_clean(s.text))
            for s in raw_segments
            if _clean(s.text)
        ]
        return segments or None
    except Exception as exc:  # noqa: BLE001
        log.warning("whisper transcription failed: %s", exc)
        return None


def get_transcript(url: str, video_path: Path | None) -> list[Segment] | None:
    segments = fetch_youtube_transcript(url)
    if segments:
        return segments
    if video_path and video_path.exists():
        return transcribe_with_whisper(video_path)
    return None
