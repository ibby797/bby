"""Optional Claude-powered re-ranking of the top heuristic candidates.

Enabled automatically when ANTHROPIC_API_KEY is set and the `anthropic`
package is installed. The heuristic engine remains the source of truth;
Claude adjusts scores (+/- blend) and contributes hook rewrites used as
on-screen title overlays.
"""

from __future__ import annotations

import json
import logging

from ..config import settings
from .virality import ScoredMoment

log = logging.getLogger(__name__)

_SYSTEM = """You are a short-form video editor who has taken dozens of clips viral \
on TikTok. You will receive candidate clip transcripts cut from a long-form video. \
For each candidate, judge how likely it is to go viral as a standalone vertical clip, \
considering: 3-second hook strength, whether it is self-contained, emotional intensity, \
curiosity gap and payoff, and shareability. Return STRICT JSON only."""


def available() -> bool:
    if not settings.anthropic_api_key:
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        log.info("anthropic package not installed; skipping AI re-rank")
        return False


def rerank(moments: list[ScoredMoment], video_title: str) -> list[ScoredMoment]:
    """Blend Claude's 0-100 judgement into the heuristic score (50/50 on the
    re-ranked subset) and attach suggested hook overlay text."""
    if not available() or not moments:
        return moments

    import anthropic

    subset = moments[: settings.ai_rerank_top]
    payload = [
        {"id": i, "duration_s": round(m.duration), "transcript": m.text[:900]}
        for i, m in enumerate(subset)
    ]
    prompt = (
        f'Video title: "{video_title}"\n\n'
        f"Candidates:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        'Return JSON: {"results": [{"id": <int>, "viral_score": <0-100>, '
        '"hook_overlay": "<max 8 words, punchy on-screen title>", '
        '"reason": "<one sentence>"}]} — one entry per candidate, JSON only.'
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=16000,
            system=_SYSTEM,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "results": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "viral_score": {"type": "number"},
                                        "hook_overlay": {"type": "string"},
                                        "reason": {"type": "string"},
                                    },
                                    "required": ["id", "viral_score", "hook_overlay", "reason"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["results"],
                        "additionalProperties": False,
                    },
                }
            },
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            log.warning("AI re-rank refused; keeping heuristic ranking")
            return moments
        text = next(b.text for b in response.content if b.type == "text")
        results = {r["id"]: r for r in json.loads(text)["results"]}
    except Exception as exc:  # noqa: BLE001 — AI layer must never break the pipeline
        log.warning("AI re-rank failed (%s); keeping heuristic ranking", exc)
        return moments

    for i, moment in enumerate(subset):
        r = results.get(i)
        if not r:
            continue
        ai_score = max(0.0, min(100.0, float(r["viral_score"])))
        moment.breakdown["ai"] = ai_score
        moment.score = moment.score * 0.5 + ai_score * 0.5
        overlay = str(r.get("hook_overlay", "")).strip()
        if overlay:
            moment.hook_text = overlay
        reason = str(r.get("reason", "")).strip()
        if reason:
            moment.reasons.insert(0, f"AI: {reason}")
            moment.reasons = moment.reasons[:5]

    moments.sort(key=lambda m: m.score, reverse=True)
    return moments
