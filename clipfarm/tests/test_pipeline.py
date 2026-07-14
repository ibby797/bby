"""Tests for the analysis pipeline (no network / ffmpeg needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipfarm.pipeline.segmenter import Sentence, build_sentences, generate_windows
from clipfarm.pipeline.transcript import Segment, extract_video_id
from clipfarm.pipeline.virality import (
    rank_moments, score_emotion, score_hook, score_retention_shape,
)
from clipfarm.rewards.campaigns import Campaign


def make_segments(n=120, seg_len=4.0):
    """Synthetic transcript: mostly filler with two 'viral' stretches."""
    segments = []
    for i in range(n):
        t = i * seg_len
        if 20 <= i <= 27:
            text = {
                20: "Nobody talks about the biggest lie in personal finance.",
                21: "I lost $50,000 in one month and here's exactly how.",
                22: "It was insane, absolutely crazy, I couldn't believe it.",
                23: "Everyone told me to just keep investing my money.",
                24: "But then suddenly the whole thing collapsed overnight.",
                25: "Turns out the secret was hidden in the contract fees.",
                26: "That's why 90% of people lose money doing this.",
                27: "And that is the one thing you should never do.",
            }[i]
        elif 60 <= i <= 66:
            text = {
                60: "Why do you always fail at building habits?",
                61: "Here's the shocking truth nobody wants to admit.",
                62: "[Laughter]",
                63: "We all do this every single morning, be honest.",
                64: "So then one day I tried the opposite approach.",
                65: "Plot twist, it worked instantly and it was unbelievable.",
                66: "The answer is because your brain hates big changes.",
            }[i]
        else:
            text = "and so we just kept talking about the weather for a while."
        segments.append(Segment(start=t, duration=seg_len, text=text))
    return segments


def test_extract_video_id():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=1") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("not a url") is None


def test_hook_scoring_prefers_strong_hooks():
    strong = score_hook("Nobody talks about the $50,000 secret that changed everything", [])
    weak = score_hook("um so yeah anyway we were kind of just talking", [])
    assert strong > 0.6
    assert weak < 0.2


def test_emotion_scoring():
    hot, laughs = score_emotion("This is insane, absolutely crazy, unbelievable! [Laughter]")
    cold, _ = score_emotion("The meeting is scheduled for Tuesday afternoon.")
    assert hot > cold
    assert laughs == 1


def test_retention_shape():
    length, pace = score_retention_shape(duration=28, words=90, min_len=12, max_len=58)
    assert length == 1.0            # 28s is in the sweet spot
    assert pace == 1.0              # 3.2 wps is ideal
    short_len, _ = score_retention_shape(duration=8, words=20, min_len=12, max_len=58)
    assert short_len < 0.2          # below minimum


def test_end_to_end_ranking_finds_planted_moments():
    segments = make_segments()
    sentences = build_sentences(segments)
    assert len(sentences) > 10

    windows = generate_windows(sentences, min_len=12, max_len=58)
    assert windows, "should generate candidate windows"

    moments = rank_moments(windows, energy=None, min_len=12, max_len=58, max_results=10)
    assert moments
    # The two planted viral stretches start near t=80s and t=240s.
    top_starts = [m.start for m in moments[:4]]
    assert any(70 <= s <= 95 for s in top_starts), f"finance moment missed: {top_starts}"
    assert any(230 <= s <= 255 for s in top_starts), f"habits moment missed: {top_starts}"
    # Scores are 0-100 and sorted descending
    assert all(0 <= m.score <= 100 for m in moments)
    assert moments == sorted(moments, key=lambda m: m.score, reverse=True)


def test_overlap_suppression():
    segments = make_segments()
    sentences = build_sentences(segments)
    windows = generate_windows(sentences, min_len=12, max_len=58)
    moments = rank_moments(windows, energy=None, min_len=12, max_len=58, max_results=50)
    for i, a in enumerate(moments):
        for b in moments[i + 1:]:
            inter = min(a.end, b.end) - max(a.start, b.start)
            if inter <= 0:
                continue
            union = max(a.end, b.end) - min(a.start, b.start)
            assert inter / union <= 0.35 + 1e-9


def test_campaign_rules():
    campaign = Campaign(rate_per_1k_views=1.5, max_payout_per_clip=100,
                        min_duration_s=10, max_duration_s=60,
                        caption_template="{hook} #fyp", required_mention="@brand")
    ok, _ = campaign.clip_is_eligible(25)
    too_short, why = campaign.clip_is_eligible(6)
    assert ok and not too_short and "minimum" in why
    caption = campaign.build_caption("The $50k mistake")
    assert "@brand" in caption and caption.startswith("The $50k mistake")
    assert campaign.estimate_earnings(40_000) == 60.0
    assert campaign.estimate_earnings(1_000_000) == 100.0  # capped


def test_captions_ass_generation(tmp_path):
    from clipfarm.pipeline.captions import build_ass
    sentences = [Sentence(start=100.0, end=104.0, text="This is absolutely insane money")]
    out = build_ass(sentences, clip_start=99.0, clip_end=110.0,
                    hook_text="The $50k mistake", font="DejaVu Sans",
                    out_path=tmp_path / "test.ass")
    content = out.read_text()
    assert "Dialogue:" in content
    assert "THE $50K MISTAKE" in content          # hook overlay, uppercased
    assert "insane" in content                    # caption card text
    assert content.count("Dialogue:") >= 2        # hook + at least one card
