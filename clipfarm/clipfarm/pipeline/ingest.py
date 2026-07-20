"""Download the source video with yt-dlp and probe its metadata."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    path: Path
    title: str
    duration: float
    uploader: str
    video_id: str
    width: int = 0
    height: int = 0


def download(url: str, dest_dir: Path, progress_hook=None) -> VideoInfo:
    """Download `url` into dest_dir as source.mp4 and return its metadata."""
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / "source.mp4"
    height = settings.max_video_height
    opts = {
        "format": f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]/b",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }
    if settings.cookies_file and Path(settings.cookies_file).exists():
        opts["cookiefile"] = settings.cookies_file
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # yt-dlp may emit .mkv/.webm when mp4 merge isn't possible
    if not out_path.exists():
        candidates = sorted(dest_dir.glob("source.*"))
        if not candidates:
            raise RuntimeError("Download finished but no output file was produced")
        out_path = candidates[0]

    probed = probe(out_path)
    return VideoInfo(
        path=out_path,
        title=info.get("title", "Untitled"),
        duration=float(info.get("duration") or probed.get("duration") or 0),
        uploader=info.get("uploader", ""),
        video_id=info.get("id", ""),
        width=probed.get("width", 0),
        height=probed.get("height", 0),
    )


def probe(path: Path) -> dict:
    """Return {duration, width, height} using ffprobe."""
    try:
        raw = subprocess.run(
            [
                settings.ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        data = json.loads(raw)
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
        )
        return {
            "duration": float(data.get("format", {}).get("duration", 0)),
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("ffprobe failed for %s: %s", path, exc)
        return {}
