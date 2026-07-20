"""Content-rewards campaign support.

Clipping platforms (Whop Content Rewards and similar) pay per 1,000 views
on approved clips — typically $0.50–$2/1k, often with a minimum payout
threshold and per-video cap. Brands reject clips that break campaign
rules, so ClipFarm attaches campaign metadata to every rendered clip and
exports a submission manifest (CSV + JSON) to track earnings.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Campaign:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = "default"
    platform: str = "whop"                 # informational
    rate_per_1k_views: float = 1.0         # USD
    min_payout: float = 0.0                # brand's minimum before review
    max_payout_per_clip: float = 0.0       # 0 = uncapped
    caption_template: str = "{hook} #fyp #viral"
    hashtags: list[str] = field(default_factory=lambda: ["fyp", "viral", "clips"])
    required_mention: str = ""             # e.g. brand @handle campaigns require
    min_duration_s: float = 10.0           # many campaigns reject clips under ~10s
    max_duration_s: float = 60.0
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Campaign":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def build_caption(self, hook: str) -> str:
        caption = self.caption_template.format(hook=hook.strip().rstrip("."))
        tags = " ".join(f"#{t.lstrip('#')}" for t in self.hashtags if t)
        parts = [caption]
        if self.required_mention:
            parts.append(self.required_mention)
        if tags and tags not in caption:
            parts.append(tags)
        return " ".join(parts)[:2200]

    def clip_is_eligible(self, duration: float) -> tuple[bool, str]:
        if duration < self.min_duration_s:
            return False, f"clip is {duration:.0f}s, campaign minimum is {self.min_duration_s:.0f}s"
        if duration > self.max_duration_s:
            return False, f"clip is {duration:.0f}s, campaign maximum is {self.max_duration_s:.0f}s"
        return True, ""

    def estimate_earnings(self, views: int) -> float:
        gross = views / 1000 * self.rate_per_1k_views
        if self.max_payout_per_clip > 0:
            gross = min(gross, self.max_payout_per_clip)
        return round(gross, 2)


class CampaignStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "campaigns.json"

    def load(self) -> list[Campaign]:
        if not self.path.exists():
            return [Campaign()]
        raw = json.loads(self.path.read_text())
        return [Campaign.from_dict(c) for c in raw]

    def save(self, campaigns: list[Campaign]) -> None:
        self.path.write_text(json.dumps([c.to_dict() for c in campaigns], indent=2))

    def get(self, campaign_id: str) -> Campaign | None:
        return next((c for c in self.load() if c.id == campaign_id), None)

    def upsert(self, campaign: Campaign) -> Campaign:
        campaigns = self.load()
        campaigns = [c for c in campaigns if c.id != campaign.id]
        campaigns.append(campaign)
        self.save(campaigns)
        return campaign


def export_manifest(clips: list[dict], campaign: Campaign, out_dir: Path) -> dict:
    """Write submission manifest (JSON + CSV) for a batch of clips."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "campaign": campaign.to_dict(),
        "clips": clips,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    csv_path = out_dir / "submissions.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "clip_file", "duration_s", "virality_score", "caption",
            "posted_url", "views", "estimated_earnings_usd", "status",
        ])
        for clip in clips:
            writer.writerow([
                clip.get("file", ""), clip.get("duration", ""),
                clip.get("score", ""), clip.get("caption", ""),
                clip.get("posted_url", ""), clip.get("views", 0),
                campaign.estimate_earnings(int(clip.get("views", 0))),
                clip.get("status", "rendered"),
            ])
    return manifest
