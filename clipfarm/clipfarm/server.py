"""FastAPI application: web UI + JSON API + automation scheduler."""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .jobs import Job, run_analysis, run_render
from .pipeline.renderer import RenderStyle
from .rewards.campaigns import Campaign, CampaignStore, export_manifest
from .tiktok.scheduler import QueueItem, Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

scheduler = Scheduler(settings.data_dir)
campaign_store = CampaignStore(settings.data_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="ClipFarm", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """When CLIPFARM_PASSWORD is set (hosted deployments), gate everything
    behind HTTP Basic auth — username `clipfarm`, password from the env."""
    if settings.password:
        expected = "Basic " + base64.b64encode(
            f"clipfarm:{settings.password}".encode()
        ).decode()
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied.encode(), expected.encode()):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="ClipFarm"'},
            )
    return await call_next(request)


# ------------------------------------------------------------- request models

class CreateJobRequest(BaseModel):
    url: str


class RenderRequest(BaseModel):
    moment_indices: list[int] | None = None
    top_n: int = Field(default=20, ge=1, le=100)
    mode: str = "blur"
    captions: bool = True
    hook_overlay: bool = True
    watermark: str = ""


class QueueRequest(BaseModel):
    clip_files: list[str] | None = None   # None = queue every rendered clip
    campaign_id: str = ""


class AutopilotRequest(BaseModel):
    url: str
    clips: int = Field(default=100, ge=1, le=100)
    campaign_id: str = ""
    mode: str = "blur"
    watermark: str = ""


class CampaignRequest(BaseModel):
    id: str = ""
    name: str = "default"
    platform: str = "whop"
    rate_per_1k_views: float = 1.0
    min_payout: float = 0.0
    max_payout_per_clip: float = 0.0
    caption_template: str = "{hook} #fyp #viral"
    hashtags: list[str] = Field(default_factory=lambda: ["fyp", "viral", "clips"])
    required_mention: str = ""
    min_duration_s: float = 10.0
    max_duration_s: float = 60.0


# --------------------------------------------------------------------- jobs

@app.post("/api/jobs")
async def create_job(req: CreateJobRequest):
    if not settings.ffmpeg_available():
        raise HTTPException(500, "ffmpeg not found — install ffmpeg and restart")
    job = Job(url=req.url.strip())
    job.save()
    asyncio.create_task(run_analysis(job))
    return job.summary()


@app.get("/api/jobs")
async def list_jobs():
    return [j.summary() for j in Job.list_all()]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = Job.load(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    data = job.summary()
    data["moments"] = job.moments
    data["clips"] = job.clips
    return data


@app.post("/api/jobs/{job_id}/render")
async def render_job(job_id: str, req: RenderRequest):
    job = Job.load(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status not in ("ready", "done"):
        raise HTTPException(409, f"job is {job.status}; wait for analysis to finish")
    if not job.moments:
        raise HTTPException(409, "no moments to render")

    indices = req.moment_indices
    if indices is None:
        indices = list(range(min(req.top_n, len(job.moments))))
    bad = [i for i in indices if i < 0 or i >= len(job.moments)]
    if bad:
        raise HTTPException(400, f"invalid moment indices: {bad}")

    style = RenderStyle(
        mode=req.mode, captions=req.captions,
        hook_overlay=req.hook_overlay, watermark=req.watermark,
    )
    asyncio.create_task(run_render(job, indices, style))
    return {"status": "rendering", "count": len(indices)}


@app.get("/api/jobs/{job_id}/clips/{clip_file}")
async def download_clip(job_id: str, clip_file: str):
    job = Job.load(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    path = (job.clips_dir / clip_file).resolve()
    if not path.is_file() or job.clips_dir.resolve() not in path.parents:
        raise HTTPException(404, "clip not found")
    return FileResponse(path, media_type="video/mp4", filename=clip_file)


# ---------------------------------------------------------------- campaigns

@app.get("/api/campaigns")
async def list_campaigns():
    return [c.to_dict() for c in campaign_store.load()]


@app.post("/api/campaigns")
async def save_campaign(req: CampaignRequest):
    campaign = Campaign.from_dict(req.model_dump())
    if not req.id:
        campaign = Campaign.from_dict({**req.model_dump(), "id": Campaign().id})
    return campaign_store.upsert(campaign).to_dict()


@app.post("/api/jobs/{job_id}/manifest")
async def build_manifest(job_id: str, req: QueueRequest):
    job = Job.load(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    campaign = campaign_store.get(req.campaign_id) or campaign_store.load()[0]
    clips = [
        {**c, "caption": campaign.build_caption(c.get("hook", ""))}
        for c in job.clips if c.get("ok")
    ]
    export_manifest(clips, campaign, job.dir)
    return {"manifest": f"/api/jobs/{job_id}/files/manifest.json",
            "csv": f"/api/jobs/{job_id}/files/submissions.csv"}


@app.get("/api/jobs/{job_id}/files/{name}")
async def job_file(job_id: str, name: str):
    job = Job.load(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    path = (job.dir / name).resolve()
    if not path.is_file() or job.dir.resolve() != path.parent:
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=name)


# -------------------------------------------------------------- automation

@app.post("/api/jobs/{job_id}/queue")
async def queue_clips(job_id: str, req: QueueRequest):
    job = Job.load(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    campaign = campaign_store.get(req.campaign_id) or campaign_store.load()[0]

    rendered = [c for c in job.clips if c.get("ok")]
    if req.clip_files is not None:
        rendered = [c for c in rendered if c["file"] in req.clip_files]
    if not rendered:
        raise HTTPException(409, "no rendered clips to queue")

    items, skipped = [], []
    for clip in rendered:
        eligible, why = campaign.clip_is_eligible(clip.get("duration", 0))
        if not eligible:
            skipped.append({"file": clip["file"], "reason": why})
            continue
        items.append(QueueItem(
            job_id=job.id,
            clip_file=clip["file"],
            caption=campaign.build_caption(clip.get("hook", "")),
        ))
    await scheduler.queue.add(items)
    return {"queued": len(items), "skipped": skipped}


@app.get("/api/queue")
async def get_queue():
    return {
        "enabled": scheduler.enabled,
        "tiktok_configured": settings.tiktok_configured(),
        "tiktok_connected": scheduler.client.connected(),
        "post_mode": settings.tiktok_post_mode,
        "posts_per_day": settings.posts_per_day,
        "items": [i.to_dict() for i in scheduler.queue.load()],
    }


@app.post("/api/queue/toggle")
async def toggle_queue():
    scheduler.enabled = not scheduler.enabled
    return {"enabled": scheduler.enabled}


@app.delete("/api/queue/{item_id}")
async def remove_queue_item(item_id: str):
    await scheduler.queue.remove(item_id)
    return {"removed": item_id}


# ---------------------------------------------------------------- autopilot

@app.post("/api/autopilot")
async def autopilot(req: AutopilotRequest):
    """One call: analyze → render top N → queue everything for posting."""
    if not settings.ffmpeg_available():
        raise HTTPException(500, "ffmpeg not found — install ffmpeg and restart")
    job = Job(url=req.url.strip())
    job.save()

    async def pipeline():
        await run_analysis(job)
        fresh = Job.load(job.id)
        if not fresh or fresh.status != "ready" or not fresh.moments:
            return
        count = min(req.clips, len(fresh.moments))
        style = RenderStyle(mode=req.mode, watermark=req.watermark)
        await run_render(fresh, list(range(count)), style)
        done = Job.load(job.id)
        if not done or done.status != "done":
            return
        campaign = campaign_store.get(req.campaign_id) or campaign_store.load()[0]
        items = []
        for clip in done.clips:
            if not clip.get("ok"):
                continue
            eligible, _ = campaign.clip_is_eligible(clip.get("duration", 0))
            if eligible:
                items.append(QueueItem(
                    job_id=done.id, clip_file=clip["file"],
                    caption=campaign.build_caption(clip.get("hook", "")),
                ))
        await scheduler.queue.add(items)
        clips_data = [
            {**c, "caption": campaign.build_caption(c.get("hook", ""))}
            for c in done.clips if c.get("ok")
        ]
        export_manifest(clips_data, campaign, done.dir)
        log.info("autopilot complete: job %s, %d clips queued", done.id, len(items))

    asyncio.create_task(pipeline())
    return {"job_id": job.id, "status": "autopilot_started"}


# ------------------------------------------------------------------- tiktok

@app.get("/api/tiktok/connect")
async def tiktok_connect():
    if not settings.tiktok_configured():
        raise HTTPException(
            409,
            "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET first "
            "(create an app at developers.tiktok.com with the Content Posting API)",
        )
    return RedirectResponse(scheduler.client.auth_url())


@app.get("/api/tiktok/callback")
async def tiktok_callback(code: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(f"<h3>TikTok authorization failed: {error or 'no code'}</h3>")
    scheduler.client.exchange_code(code)
    return RedirectResponse("/?tiktok=connected")


# ------------------------------------------------------------------ settings

@app.get("/api/status")
async def status():
    return {
        "ffmpeg": settings.ffmpeg_available(),
        "ai_rerank": bool(settings.anthropic_api_key),
        "tiktok_configured": settings.tiktok_configured(),
        "tiktok_connected": scheduler.client.connected(),
        "post_mode": settings.tiktok_post_mode,
        "posts_per_day": settings.posts_per_day,
        "max_clips": settings.max_clips,
    }


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
