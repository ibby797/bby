"""Automation queue + scheduler.

Rendered clips are enqueued; a background task drip-posts them to TikTok
at `posts_per_day` spread across the configured posting window with
random jitter, so output looks organic and stays under TikTok's
25-posts/day API cap. Without TikTok API credentials the queue still
runs in *export mode*: clips are marked ready-to-post so you can upload
them manually (or via the mobile app) while everything else stays
automated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from ..config import settings
from .client import TikTokClient, TikTokError

log = logging.getLogger(__name__)


@dataclass
class QueueItem:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    job_id: str = ""
    clip_file: str = ""
    caption: str = ""
    status: str = "queued"          # queued | posting | posted | ready_manual | failed
    scheduled_at: float = 0.0
    posted_at: float = 0.0
    publish_id: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PostQueue:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "queue.json"
        self._lock = asyncio.Lock()

    def load(self) -> list[QueueItem]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [QueueItem(**item) for item in raw]

    def save(self, items: list[QueueItem]) -> None:
        self.path.write_text(json.dumps([i.to_dict() for i in items], indent=2))

    async def add(self, items: list[QueueItem]) -> None:
        async with self._lock:
            existing = self.load()
            existing.extend(items)
            self._reschedule(existing)
            self.save(existing)

    async def remove(self, item_id: str) -> None:
        async with self._lock:
            self.save([i for i in self.load() if i.id != item_id])

    async def update(self, item: QueueItem) -> None:
        async with self._lock:
            items = self.load()
            items = [item if i.id == item.id else i for i in items]
            self.save(items)

    def _reschedule(self, items: list[QueueItem]) -> None:
        """Assign posting times: posts_per_day spread over the daily window."""
        pending = [i for i in items if i.status == "queued"]
        window_hours = max(1, settings.post_window_end - settings.post_window_start)
        interval = window_hours * 3600 / settings.posts_per_day

        now = time.time()
        last = max((i.scheduled_at for i in items if i.scheduled_at), default=now)
        cursor = max(now, last)
        for item in pending:
            if item.scheduled_at:
                continue
            cursor += interval + random.uniform(-1, 1) * settings.post_jitter_minutes * 60
            cursor = max(cursor, now + 60)
            item.scheduled_at = self._clamp_to_window(cursor)

    @staticmethod
    def _clamp_to_window(ts: float) -> float:
        """Push a timestamp forward into the allowed posting window."""
        dt = datetime.fromtimestamp(ts)
        if settings.post_window_start <= dt.hour < settings.post_window_end:
            return ts
        # move to the start of the next window
        target = dt.replace(hour=settings.post_window_start, minute=random.randint(0, 45),
                            second=0, microsecond=0)
        if dt.hour >= settings.post_window_end:
            target = target.replace(day=dt.day)
            target = datetime.fromtimestamp(target.timestamp() + 86400)
        return target.timestamp()


class Scheduler:
    """Background loop that drains the queue on schedule."""

    def __init__(self, data_dir: Path):
        self.queue = PostQueue(data_dir)
        self.client = TikTokClient(data_dir)
        self.data_dir = data_dir
        self.enabled = True
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def _posted_today(self, items: list[QueueItem]) -> int:
        midnight = datetime.now().replace(hour=0, minute=0, second=0).timestamp()
        return sum(1 for i in items if i.status == "posted" and i.posted_at >= midnight)

    async def _run(self) -> None:
        log.info("automation scheduler started (%d posts/day, window %02d:00-%02d:00)",
                 settings.posts_per_day, settings.post_window_start, settings.post_window_end)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("scheduler tick failed: %s", exc)
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        if not self.enabled:
            return
        items = self.queue.load()
        if self._posted_today(items) >= settings.posts_per_day:
            return
        now = time.time()
        due = [i for i in items if i.status == "queued" and i.scheduled_at <= now]
        if not due:
            return
        item = due[0]

        clip_path = self.data_dir / "jobs" / item.job_id / "clips" / item.clip_file
        if not clip_path.exists():
            item.status = "failed"
            item.error = "clip file missing"
            await self.queue.update(item)
            return

        if not (settings.tiktok_configured() and self.client.connected()):
            # Export mode: surface the clip as ready for manual posting.
            item.status = "ready_manual"
            item.posted_at = now
            await self.queue.update(item)
            log.info("clip ready for manual posting: %s", item.clip_file)
            return

        item.status = "posting"
        await self.queue.update(item)
        try:
            result = await asyncio.to_thread(self.client.post_video, clip_path, item.caption)
            item.status = "posted"
            item.publish_id = result["publish_id"]
            item.posted_at = time.time()
            log.info("posted %s → publish_id=%s", item.clip_file, item.publish_id)
        except TikTokError as exc:
            item.status = "failed"
            item.error = str(exc)[:500]
            log.error("post failed for %s: %s", item.clip_file, exc)
        await self.queue.update(item)
