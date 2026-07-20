"""Virality scoring engine.

Every feature here maps to a documented driver of short-form virality
(see docs/VIRAL_RESEARCH.md for sources):

- HOOK (weight 30): watch-time in the first ~3 seconds is the strongest
  ranking signal on TikTok; ~63% of top-CTR videos hook within 3s.
- RETENTION SHAPE (weight 20): completion rate drives distribution (the
  virality threshold is ~70% completion in 2026), so clip length and
  speech pace are scored against the 20–35s completion sweet spot.
- EMOTION (weight 20): high-arousal emotion (awe, anger, surprise,
  humour) is the classic Berger & Milkman predictor of sharing.
- PAYOFF / CURIOSITY (weight 15): an opened loop that resolves inside the
  clip sustains watch-through; shares and saves now outweigh likes.
- AUDIO ENERGY (weight 10): loudness spikes (laughter, shouting) flag
  reaction moments transcripts miss.
- SHARE TRIGGERS (weight 5): money, controversy, relatability and
  second-person address correlate with shares/saves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .audio import EnergyTimeline
from .segmenter import Window

# ---------------------------------------------------------------- lexicons

HOOK_PATTERNS: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"^(how|why|what|when|who|where)\b", re.I), 2.0, "opens with a question word"),
    (re.compile(r"\?", re.I), 1.2, "asks a question"),
    (re.compile(r"\b(nobody|no one|never|nothing|everyone|everybody|always)\b", re.I), 1.6, "absolute claim"),
    (re.compile(r"\b(secret|truth|hidden|exposed|banned|illegal|forbidden)\b", re.I), 2.0, "curiosity gap"),
    (re.compile(r"\b(you|your)\b", re.I), 1.2, "addresses the viewer directly"),
    (re.compile(r"\$[\d,]+|\b\d[\d,]*\s*(dollars|million|billion|k|grand)\b", re.I), 1.8, "specific money amount"),
    (re.compile(r"\b\d+%|\b\d+\s*(times|x)\b", re.I), 1.4, "specific number/stat"),
    (re.compile(r"\b(craziest|insane|unbelievable|shocking|worst|best|biggest)\b", re.I), 1.6, "superlative"),
    (re.compile(r"\b(stop|wait|listen|imagine|here'?s)\b", re.I), 1.4, "pattern interrupt"),
    (re.compile(r"\b(i (was|am|got|lost|made|quit|almost))\b", re.I), 1.2, "first-person stakes"),
    (re.compile(r"\b(mistake|wrong|lie|lied|scam|myth)\b", re.I), 1.6, "challenges a belief"),
]

EMOTION_WORDS = {
    # high-arousal positive
    "insane": 2, "crazy": 2, "unbelievable": 2, "incredible": 2, "amazing": 1.5,
    "hilarious": 2, "epic": 1.5, "wild": 1.5, "awesome": 1, "love": 1, "perfect": 1,
    "mind-blowing": 2, "genius": 1.5, "legendary": 1.5, "beautiful": 1,
    # high-arousal negative
    "terrifying": 2, "horrible": 1.5, "disaster": 2, "furious": 2, "hate": 1.5,
    "disgusting": 2, "scary": 1.5, "brutal": 1.5, "insulting": 1.5, "outrageous": 2,
    "shocking": 2, "awful": 1.5, "nightmare": 2, "ruined": 1.5, "destroyed": 1.5,
    # awe / surprise
    "wow": 1.5, "whoa": 1.5, "omg": 2, "literally": 0.5, "actually": 0.5,
    "unreal": 2, "impossible": 1.5, "crazy?": 2,
}

STORY_MARKERS = re.compile(
    r"\b(so then|and then|suddenly|one day|out of nowhere|long story short|"
    r"turns out|plot twist|that's when|until|but then|guess what)\b", re.I
)

PAYOFF_MARKERS = re.compile(
    r"\b(turns out|that's why|that's how|the answer|the reason|because|"
    r"so what happened|in the end|and that is|which means|the result|moral of)\b", re.I
)

MONEY_WORDS = re.compile(
    r"\b(money|rich|broke|salary|profit|income|invest(ing|ment)?|passive|"
    r"million(aire)?|billion(aire)?|side hustle|net worth|paid|payout)\b|\$[\d,]+", re.I
)

CONTROVERSY_WORDS = re.compile(
    r"\b(controversial|cancelled|canceled|drama|beef|exposed|called out|"
    r"disagree|unpopular opinion|hot take|debate|banned|lawsuit|fired)\b", re.I
)

RELATABLE_WORDS = re.compile(
    r"\b(everybody|we all|you know when|admit it|be honest|nobody talks about|"
    r"relatable|every single|all of us|let's be real)\b", re.I
)

LAUGHTER = re.compile(r"\[laughter\]|\[applause\]|\bhaha+\b|\blol\b", re.I)

FILLER_OPENERS = re.compile(
    r"^(um+|uh+|so um|yeah so|okay so|alright so|you know)\b", re.I
)


@dataclass
class ScoredMoment:
    start: float
    end: float
    text: str
    hook_text: str
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "text": self.text,
            "hook_text": self.hook_text,
            "score": round(self.score, 1),
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
            "reasons": self.reasons,
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def score_hook(hook_text: str, reasons: list[str]) -> float:
    """First ~12 words. Scored 0..1."""
    raw = 0.0
    for pattern, weight, label in HOOK_PATTERNS:
        if pattern.search(hook_text):
            raw += weight
            if len(reasons) < 3:
                reasons.append(f"Hook: {label}")
    if FILLER_OPENERS.search(hook_text):
        raw -= 1.5
    return _clamp(raw / 5.0)


def score_emotion(text: str) -> tuple[float, int]:
    words = re.findall(r"[a-zA-Z'-]+", text.lower())
    if not words:
        return 0.0, 0
    hits = sum(EMOTION_WORDS.get(w, 0) for w in words)
    laughter_hits = len(LAUGHTER.findall(text))
    hits += laughter_hits * 2
    # normalize per 50 words so long clips don't win by volume alone
    density = hits / max(len(words) / 50, 1)
    return _clamp(density / 4.0), laughter_hits


def score_retention_shape(duration: float, words: int,
                          min_len: float, max_len: float) -> tuple[float, float]:
    """(length_score, pace_score), each 0..1.

    Completion rate is what the algorithm rewards; 20–35s clips are the
    sweet spot — long enough to monetize on rewards campaigns, short
    enough that 70%+ of viewers finish.
    """
    if duration < min_len or duration > max_len:
        length = 0.1
    elif 20 <= duration <= 35:
        length = 1.0
    elif duration < 20:
        length = 0.6 + 0.4 * (duration - min_len) / max(20 - min_len, 1)
    else:  # 35..max
        length = 1.0 - 0.5 * (duration - 35) / max(max_len - 35, 1)

    wps = words / duration if duration > 0 else 0
    # 2.3–4.2 words/sec reads as energetic but followable
    if 2.3 <= wps <= 4.2:
        pace = 1.0
    elif wps < 2.3:
        pace = _clamp(wps / 2.3)
    else:
        pace = _clamp(1.0 - (wps - 4.2) / 3.0)
    return length, pace


def score_payoff(text: str, hook_text: str) -> float:
    tail = text[len(hook_text):]
    raw = 0.0
    if PAYOFF_MARKERS.search(tail):
        raw += 0.6
    if STORY_MARKERS.search(text):
        raw += 0.4
    # numbers appearing later in the clip = concrete payoff delivered
    if re.search(r"\d", tail):
        raw += 0.2
    return _clamp(raw)


def score_share_triggers(text: str, reasons: list[str]) -> float:
    raw = 0.0
    if MONEY_WORDS.search(text):
        raw += 0.4
        reasons.append("Topic: money/wealth (high share rate)")
    if CONTROVERSY_WORDS.search(text):
        raw += 0.4
        reasons.append("Topic: controversy/hot take (drives comments)")
    if RELATABLE_WORDS.search(text):
        raw += 0.3
        reasons.append("Relatability trigger (drives shares/saves)")
    return _clamp(raw)


WEIGHTS = {
    "hook": 30.0,
    "length": 10.0,
    "pace": 10.0,
    "emotion": 20.0,
    "payoff": 15.0,
    "energy": 10.0,
    "share": 5.0,
}


def score_window(window: Window, energy: EnergyTimeline | None,
                 min_len: float, max_len: float) -> ScoredMoment:
    reasons: list[str] = []
    text = window.text
    hook_text = " ".join(text.split()[:12])

    hook = score_hook(hook_text, reasons)
    emotion, laughs = score_emotion(text)
    length, pace = score_retention_shape(
        window.duration, len(text.split()), min_len, max_len
    )
    payoff = score_payoff(text, hook_text)
    share = score_share_triggers(text, reasons)

    if energy is not None:
        mean_delta, peak_delta = energy.window_stats(window.start, window.end)
        # +3 dB above baseline is a clear spike; scale peak into 0..1
        energy_score = _clamp((peak_delta / 6.0) * 0.6 + (mean_delta / 4.0) * 0.4 + 0.3)
        if peak_delta >= 3.0:
            reasons.append("Audio spike — laughter/excitement moment")
    else:
        energy_score = 0.5  # neutral when no audio analysis available

    if laughs:
        reasons.append("Contains laughter")
    if emotion > 0.5:
        reasons.append("High emotional intensity")
    if payoff > 0.5:
        reasons.append("Story arc with a payoff inside the clip")
    if 20 <= window.duration <= 35:
        reasons.append(f"{window.duration:.0f}s — completion-rate sweet spot")

    breakdown = {
        "hook": hook * WEIGHTS["hook"],
        "length": length * WEIGHTS["length"],
        "pace": pace * WEIGHTS["pace"],
        "emotion": emotion * WEIGHTS["emotion"],
        "payoff": payoff * WEIGHTS["payoff"],
        "energy": energy_score * WEIGHTS["energy"],
        "share_triggers": share * WEIGHTS["share"],
    }
    total = sum(breakdown.values())

    return ScoredMoment(
        start=window.start, end=window.end, text=text, hook_text=hook_text,
        score=total, breakdown=breakdown, reasons=reasons[:5],
    )


def rank_moments(windows: list[Window], energy: EnergyTimeline | None,
                 min_len: float, max_len: float,
                 max_results: int, overlap_threshold: float = 0.35) -> list[ScoredMoment]:
    """Score all candidate windows, then greedily keep the best
    non-overlapping ones (IoU-based suppression)."""
    scored = [score_window(w, energy, min_len, max_len) for w in windows]
    scored.sort(key=lambda m: m.score, reverse=True)

    kept: list[ScoredMoment] = []
    for moment in scored:
        if len(kept) >= max_results:
            break
        clashes = False
        for existing in kept:
            inter = min(moment.end, existing.end) - max(moment.start, existing.start)
            if inter <= 0:
                continue
            union = max(moment.end, existing.end) - min(moment.start, existing.start)
            if inter / union > overlap_threshold:
                clashes = True
                break
        if not clashes:
            kept.append(moment)
    return kept


def energy_only_moments(energy: EnergyTimeline, video_duration: float,
                        min_len: float, max_len: float,
                        max_results: int) -> list[ScoredMoment]:
    """Fallback when no transcript exists: slide fixed windows over the
    energy timeline and rank purely on loudness dynamics."""
    target = min(max(28.0, min_len), max_len)
    step = 8.0
    moments: list[ScoredMoment] = []
    t = 0.0
    while t + target <= video_duration:
        mean_delta, peak_delta = energy.window_stats(t, t + target)
        score = _clamp((peak_delta / 6.0) * 0.6 + (mean_delta / 4.0) * 0.4 + 0.2) * 100
        moments.append(ScoredMoment(
            start=t, end=t + target, text="", hook_text="",
            score=score,
            breakdown={"energy": score},
            reasons=["Ranked on audio energy only (no transcript available)"],
        ))
        t += step

    moments.sort(key=lambda m: m.score, reverse=True)
    kept: list[ScoredMoment] = []
    for m in moments:
        if len(kept) >= max_results:
            break
        if all(min(m.end, k.end) - max(m.start, k.start) <= target * 0.3 for k in kept):
            kept.append(m)
    return kept
