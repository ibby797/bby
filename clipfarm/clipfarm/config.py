"""Configuration: environment variables first, optional clipfarm.yaml second."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILE = os.environ.get("CLIPFARM_CONFIG", "clipfarm.yaml")


@dataclass
class Settings:
    # Storage
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CLIPFARM_DATA", "data")))

    # Rendering
    ffmpeg: str = os.environ.get("CLIPFARM_FFMPEG", "ffmpeg")
    ffprobe: str = os.environ.get("CLIPFARM_FFPROBE", "ffprobe")
    render_concurrency: int = int(os.environ.get("CLIPFARM_RENDER_CONCURRENCY", "2"))
    max_video_height: int = int(os.environ.get("CLIPFARM_MAX_HEIGHT", "1080"))
    caption_font: str = os.environ.get("CLIPFARM_FONT", "DejaVu Sans")

    # Web hosting
    # Set a password when exposing ClipFarm on the internet — the whole UI/API
    # is then gated behind HTTP Basic auth (user: clipfarm).
    password: str = os.environ.get("CLIPFARM_PASSWORD", "")
    # Path to a Netscape-format cookies.txt exported from your browser.
    # Needed on most cloud hosts: YouTube rate-limits datacenter IPs.
    cookies_file: str = os.environ.get("CLIPFARM_COOKIES", "")

    # Analysis
    min_clip_seconds: float = float(os.environ.get("CLIPFARM_MIN_CLIP", "12"))
    max_clip_seconds: float = float(os.environ.get("CLIPFARM_MAX_CLIP", "58"))
    max_clips: int = int(os.environ.get("CLIPFARM_MAX_CLIPS", "100"))

    # Optional Claude API re-ranking of top candidates
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("CLIPFARM_ANTHROPIC_MODEL", "claude-opus-4-8")
    ai_rerank_top: int = int(os.environ.get("CLIPFARM_AI_RERANK_TOP", "40"))

    # TikTok Content Posting API (official). Leave empty to run in export-only mode.
    tiktok_client_key: str = os.environ.get("TIKTOK_CLIENT_KEY", "")
    tiktok_client_secret: str = os.environ.get("TIKTOK_CLIENT_SECRET", "")
    tiktok_redirect_uri: str = os.environ.get(
        "TIKTOK_REDIRECT_URI", "http://localhost:8000/api/tiktok/callback"
    )
    # "direct" publishes immediately (audited apps); "inbox" drops into the TikTok
    # app's drafts so the user taps Post (works for unaudited apps, but content
    # from unaudited clients is restricted to SELF_ONLY visibility by TikTok).
    tiktok_post_mode: str = os.environ.get("TIKTOK_POST_MODE", "inbox")

    # Automation schedule
    posts_per_day: int = int(os.environ.get("CLIPFARM_POSTS_PER_DAY", "6"))
    post_window_start: int = int(os.environ.get("CLIPFARM_POST_WINDOW_START", "9"))   # local hour
    post_window_end: int = int(os.environ.get("CLIPFARM_POST_WINDOW_END", "23"))
    post_jitter_minutes: int = int(os.environ.get("CLIPFARM_POST_JITTER_MIN", "25"))

    def __post_init__(self) -> None:
        cfg = Path(CONFIG_FILE)
        if cfg.exists():
            overrides = yaml.safe_load(cfg.read_text()) or {}
            for key, value in overrides.items():
                if hasattr(self, key) and value is not None:
                    current = getattr(self, key)
                    if isinstance(current, Path):
                        value = Path(value)
                    setattr(self, key, type(current)(value) if not isinstance(current, Path) else value)
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "jobs").mkdir(exist_ok=True)
        # TikTok's Content Posting API caps publishing at 25 videos/account/day.
        self.posts_per_day = max(1, min(self.posts_per_day, 25))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def ffmpeg_available(self) -> bool:
        return shutil.which(self.ffmpeg) is not None

    def tiktok_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)


settings = Settings()
