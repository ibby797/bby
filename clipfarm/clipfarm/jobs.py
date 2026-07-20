"""Job manager: one job per source video. Persists state to disk so the
UI can poll and jobs survive restarts."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .config import settings
from .pipeline import ai_scorer, ingest
from .pipeline.audio import analyze_energy
from .pipeline.renderer import RenderStyle, render_batch
from .pipeline.segmenter import Sentence, build_sentences, generate_windows
from .pipeline.transcript import get_transcript
from .pipeline.virality import energy_only_moments, rank_moments

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    url: str = ""
    title: str = ""
    status: str = "created"     # created | downloading | transcribing | analyzing | ready | rendering | done | error
    error: str = ""
    progress: str = ""
    duration: float = 0.0
    created_at: float = field(default_factory=time.time)
    moments: list[dict] = field(default_factory=list)
    sentences: list[dict] = field(default_factory=list)
    clips: list[dict] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return settings.jobs_dir / self.id

    @property
    def clips_dir(self) -> Path:
        return self.dir / "clips"

    @property
    def source(self) -> Path:
        return self.dir / "source.mp4"

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "job.json").write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, job_id: str) -> "Job | None":
        path = settings.jobs_dir / job_id / "job.json"
        if not path.exists():
            return None
        return cls(**json.loads(path.read_text()))

    @classmethod
    def list_all(cls) -> list["Job"]:
        jobs = []
        for job_file in settings.jobs_dir.glob("*/job.json"):
            try:
                jobs.append(cls(**json.loads(job_file.read_text())))
            except Exception:  # noqa: BLE001
                continue
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def summary(self) -> dict:
        return {
            "id": self.id, "url": self.url, "title": self.title,
            "status": self.status, "error": self.error, "progress": self.progress,
            "duration": self.duration, "created_at": self.created_at,
            "moments": len(self.moments), "clips": len(self.clips),
        }


async def run_analysis(job: Job) -> None:
    """download → transcript → audio energy → score → (optional AI rerank)."""
    try:
        job.status = "downloading"
        job.progress = "Downloading video…"
        job.save()
        info = await asyncio.to_thread(ingest.download, job.url, job.dir)
        job.title = info.title
        job.duration = info.duration
        source = info.path

        job.status = "transcribing"
        job.progress = "Fetching transcript…"
        job.save()
        segments = await asyncio.to_thread(get_transcript, job.url, source)

        job.status = "analyzing"
        job.progress = "Analyzing audio energy…"
        job.save()
        energy = await asyncio.to_thread(analyze_energy, source)

        job.progress = "Scoring viral moments…"
        job.save()
        if segments:
            sentences = build_sentences(segments)
            windows = generate_windows(
                sentences, settings.min_clip_seconds, settings.max_clip_seconds
            )
            moments = rank_moments(
                windows, energy,
                settings.min_clip_seconds, settings.max_clip_seconds,
                max_results=settings.max_clips,
            )
            if ai_scorer.available():
                job.progress = "AI re-ranking top candidates…"
                job.save()
                moments = await asyncio.to_thread(ai_scorer.rerank, moments, job.title)
            job.sentences = [
                {"start": s.start, "end": s.end, "text": s.text} for s in sentences
            ]
        elif energy:
            moments = energy_only_moments(
                energy, job.duration,
                settings.min_clip_seconds, settings.max_clip_seconds,
                max_results=settings.max_clips,
            )
            job.sentences = []
        else:
            raise RuntimeError(
                "No transcript available and audio analysis failed — "
                "install faster-whisper or check that ffmpeg is installed"
            )

        job.moments = [m.to_dict() for m in moments]
        job.status = "ready"
        job.progress = f"Found {len(job.moments)} candidate moments"
        job.save()
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis failed for job %s", job.id)
        job.status = "error"
        job.error = str(exc)[:800]
        job.save()


async def run_render(job: Job, moment_indices: list[int], style: RenderStyle) -> None:
    """Render selected moments into clips/ inside the job directory."""
    from .pipeline.virality import ScoredMoment

    try:
        job.status = "rendering"
        job.progress = f"Rendering 0/{len(moment_indices)} clips…"
        job.save()

        sentences = [Sentence(**s) for s in job.sentences]
        render_jobs = []
        for rank, idx in enumerate(moment_indices, start=1):
            m = job.moments[idx]
            moment = ScoredMoment(
                start=m["start"], end=m["end"], text=m["text"],
                hook_text=m["hook_text"], score=m["score"],
            )
            out = job.clips_dir / f"clip_{rank:03d}_s{int(m['score'])}.mp4"
            clip_sentences = [
                s for s in sentences if s.end > moment.start and s.start < moment.end
            ]
            render_jobs.append((moment, clip_sentences, out))

        def on_progress(done: int, total: int) -> None:
            job.progress = f"Rendering {done}/{total} clips…"
            job.save()

        results = await render_batch(job.source, render_jobs, style, on_progress)

        clips = []
        for (moment, _s, out), result, idx in zip(render_jobs, results, moment_indices):
            clips.append({
                "file": out.name,
                "ok": result["ok"],
                "error": result.get("error", ""),
                "moment_index": idx,
                "start": moment.start,
                "end": moment.end,
                "duration": round(moment.duration, 1),
                "score": round(moment.score, 1),
                "hook": moment.hook_text,
                "status": "rendered" if result["ok"] else "failed",
            })
        job.clips = clips
        ok_count = sum(1 for c in clips if c["ok"])
        job.status = "done"
        job.progress = f"Rendered {ok_count}/{len(clips)} clips"
        job.save()
    except Exception as exc:  # noqa: BLE001
        log.exception("render failed for job %s", job.id)
        job.status = "error"
        job.error = str(exc)[:800]
        job.save()
