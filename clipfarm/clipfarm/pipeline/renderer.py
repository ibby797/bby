"""ffmpeg rendering: cut a scored moment into a 9:16 1080x1920 clip with
burned-in captions and loudness-normalized audio, ready for TikTok."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from .captions import build_ass
from .segmenter import Sentence
from .virality import ScoredMoment

log = logging.getLogger(__name__)


@dataclass
class RenderStyle:
    mode: str = "blur"           # "blur" (blurred pad) or "crop" (center crop)
    captions: bool = True
    hook_overlay: bool = True
    watermark: str = ""          # e.g. "@yourhandle" bottom of frame


def _vf(style: RenderStyle, ass_path: Path | None) -> str:
    if style.mode == "crop":
        chain = "scale=-2:1920,crop=min(iw\\,1080):1920,scale=1080:1920,setsar=1"
    else:
        chain = (
            "split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=24:6[bgb];"
            "[fg]scale=1080:-2[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if ass_path is not None:
        escaped = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        chain += f",ass='{escaped}'"
    if style.watermark:
        text = style.watermark.replace("\\", "").replace("'", "").replace(":", r"\:")
        chain += (
            f",drawtext=text='{text}':fontcolor=white@0.6:fontsize=38:"
            "x=(w-text_w)/2:y=h-140:borderw=2:bordercolor=black@0.6"
        )
    return chain


def render_clip_sync(source: Path, moment: ScoredMoment,
                     sentences: list[Sentence], out_path: Path,
                     style: RenderStyle) -> Path:
    """Blocking render of a single clip."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ass_path: Path | None = None
    if style.captions and sentences:
        ass_path = out_path.with_suffix(".ass")
        build_ass(
            sentences=sentences,
            clip_start=moment.start,
            clip_end=moment.end,
            hook_text=moment.hook_text if style.hook_overlay else "",
            font=settings.caption_font,
            out_path=ass_path,
        )

    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{moment.start:.2f}", "-to", f"{moment.end:.2f}",
        "-i", str(source),
        "-vf", _vf(style, ass_path),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg failed for {out_path.name}: {proc.stderr[-800:]}")
    if ass_path and ass_path.exists():
        ass_path.unlink(missing_ok=True)
    return out_path


async def render_batch(source: Path, jobs: list[tuple[ScoredMoment, list[Sentence], Path]],
                       style: RenderStyle, on_progress=None) -> list[dict]:
    """Render many clips with bounded concurrency. Returns per-clip results."""
    semaphore = asyncio.Semaphore(max(1, settings.render_concurrency))
    results: list[dict] = []
    done = 0

    async def one(moment: ScoredMoment, sentences: list[Sentence], out: Path) -> dict:
        nonlocal done
        async with semaphore:
            try:
                await asyncio.to_thread(render_clip_sync, source, moment, sentences, out, style)
                result = {"file": out.name, "ok": True}
            except Exception as exc:  # noqa: BLE001
                log.error("render failed: %s", exc)
                result = {"file": out.name, "ok": False, "error": str(exc)[:500]}
        done += 1
        if on_progress:
            on_progress(done, len(jobs))
        return result

    results = await asyncio.gather(*(one(m, s, p) for m, s, p in jobs))
    return list(results)
