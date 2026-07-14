# Why clips go viral — research notes & how ClipFarm uses them

This document summarizes the research behind ClipFarm's virality scoring
engine (`clipfarm/pipeline/virality.py`). Each finding maps to a concrete
scoring feature and weight.

## 1. The 3-second hook decides everything

- Watch time in the first ~3 seconds is the **strongest ranking signal**
  on TikTok. When a video is posted it is shown to a small test audience
  first; if most swipe away in the first seconds the video stalls
  (often under a few hundred views) and dies.
- ~63% of the highest click-through videos hook within the first three
  seconds.
- Rough retention benchmarks: ~70% of viewers still watching past 3s
  signals wide-reach potential; ~60% at 15s; ~50% at 30s.

**ClipFarm mapping → `hook` feature, weight 30/100.** The first ~12 words
of every candidate window are scanned for proven hook patterns: question
openers, curiosity-gap words ("secret", "banned", "exposed"), absolute
claims ("nobody", "never"), direct viewer address ("you"), specific money
amounts/stats, superlatives, pattern interrupts ("wait", "imagine"),
first-person stakes, and belief challenges ("the biggest lie about…").
Filler openers ("um, so yeah…") are penalized. The hook line is also
burned onto the first 3 seconds of the clip as a title overlay, and the
AI re-ranker (optional) rewrites it into a punchier on-screen title.

## 2. Completion rate drives distribution

- Watch time + completion are estimated at **40–50% of the entire ranking
  decision**. In 2026 the completion-rate threshold for viral distribution
  has risen to roughly **70%** (up from ~50% in 2024). If the sampled
  audience finishes ~70%+ of the video, TikTok widens distribution.
- Practical consequence: **shorter, denser clips complete more**. The
  20–35s range is the sweet spot — long enough to carry a story and count
  as substantial for rewards campaigns, short enough to finish.

**ClipFarm mapping → `length` (10) + `pace` (10).** Candidate windows are
generated at ~22s / ~34s / ~50s targets, always ending on sentence
boundaries so clips feel self-contained. 20–35s scores highest; speech
density of 2.3–4.2 words/sec (energetic but followable) scores highest.

## 3. High-arousal emotion gets shared

- The classic Berger & Milkman finding (verified repeatedly on short-form):
  content that evokes **high-arousal emotions** — awe, amusement/laughter,
  anger, anxiety, surprise — is shared significantly more than low-arousal
  content (sadness, contentment).
- Laughter is the single most reliable clip signal in podcast/stream
  clipping.

**ClipFarm mapping → `emotion` (20) + `energy` (10).** Transcript text is
scored against a weighted high-arousal lexicon plus `[laughter]`/
`[applause]` annotations. Separately, one ffmpeg `ebur128` pass builds a
loudness timeline; windows whose momentary loudness spikes ≥3 dB above the
video's baseline (laughter, shouting, excitement) get an energy bonus —
this catches reaction moments the transcript can't see.

## 4. Open loops and payoffs sustain retention

- Clips that **open a curiosity gap and resolve it inside the clip**
  ("…and what happened next changed everything → payoff") hold viewers to
  the end, which feeds the completion-rate signal above.
- Story markers ("so then", "suddenly", "turns out", "plot twist")
  indicate narrative arcs; a payoff near the end ("that's why…", concrete
  numbers/results) rewards the watch.

**ClipFarm mapping → `payoff` (15).** Windows containing story markers and
resolution markers *after* the hook — especially concrete numbers late in
the clip — score higher.

## 5. Shares and saves now beat likes

- Shares and saves have overtaken likes in algorithmic weight: a video
  with 100 shares typically outperforms one with 1,000 likes and no
  shares. Topics that reliably trigger shares/saves: **money**,
  **controversy/hot takes** (drives comments too), and **relatability**
  ("we all do this…" → tag-a-friend behaviour).

**ClipFarm mapping → `share_triggers` (5).** Regex detectors for
money/wealth topics, controversy language, and relatability phrases.

## 6. Content rewards economics (why 100 clips)

- Clipping campaigns (e.g. Whop Content Rewards) pay per 1,000 views on
  **approved** clips — typically **$0.50–$2 / 1k views**, sometimes with a
  flat-fee bonus. Campaigns set a minimum payout (your clip needs enough
  views to clear it) and a per-clip cap, and brands **reject clips that
  break the campaign rules** (length, mention, watermark, caption).
- Views on short-form are a power-law lottery: most clips do modest
  numbers, a few break out. **Volume is the strategy** — posting many
  well-formed clips maximizes the chance that several catch a
  distribution wave, and every extra view on any of them is paid.

**ClipFarm mapping →** batch rendering of up to 100 clips per video, a
campaign config (rate, min/max duration, caption template, required
mention) that pre-filters ineligible clips before queueing, and a
submissions manifest (CSV/JSON) for tracking payouts.

## 7. Posting cadence & automation constraints

- Steady drip-posting outperforms dump-posting: each video gets its own
  test-audience window, and accounts that flood get suppressed.
- TikTok's **Content Posting API** is the only ToS-compliant automation
  path. Constraints that shape ClipFarm's design:
  - OAuth per creator account; access tokens expire every 24h (auto-refresh).
  - Max **25 API posts per account per day**; files up to 1 GB.
  - Two modes: **Direct Post** (publish/schedule without user action) and
    **Inbox/Draft** (video lands in the user's TikTok drafts to tap-post).
  - **Unaudited developer apps can only direct-post as private
    (SELF_ONLY)**. Passing TikTok's app audit unlocks public direct
    posting.

**ClipFarm mapping →** the scheduler drip-posts N clips/day (default 6,
hard-capped at 25) inside a configurable daytime window with random
jitter. Without API credentials it runs in export mode (clips marked
ready on schedule for manual posting) — same pipeline, zero ToS risk.

## Score composition

| Feature        | Weight | Source signal |
|----------------|-------:|---------------|
| Hook           | 30     | transcript (first ~12 words) |
| Emotion        | 20     | transcript lexicon + laughter markers |
| Length         | 10     | window duration vs 20–35s sweet spot |
| Pace           | 10     | words/second |
| Payoff/story   | 15     | story + resolution markers after hook |
| Audio energy   | 10     | ffmpeg ebur128 loudness spikes |
| Share triggers | 5      | money/controversy/relatability topics |

Optional: when `ANTHROPIC_API_KEY` is set, the top candidates are
re-ranked by Claude (50/50 blend with the heuristic score) and each clip
gets an AI-written on-screen hook title.

## Sources

- [Hootsuite — How the TikTok algorithm works in 2026](https://blog.hootsuite.com/tiktok-algorithm/)
- [Hansen Insights — The 3-Second Hook: Why TikTok Videos Win or Die in 2026](https://hansencommerce.com/insights-tiktok-hook-3-seconds)
- [Socialync — TikTok Algorithm 2026: What Works Now](https://www.socialync.io/blog/tiktok-algorithm-2026-what-works-now)
- [go-viral.app — TikTok Algorithm 2026: Watch Time, Completion Rate & How to Go Viral](https://www.go-viral.app/blog/tiktok-algorithm-2026/)
- [Darkroom — How TikTok's algorithm works in 2026 and 15 tactics to go viral](https://www.darkroomagency.com/observatory/how-tiktok%E2%80%99s-algorithm-works-in-2026-and-15-tactics-to-go-viral)
- [Sprout Social — How the TikTok Algorithm Works in 2026](https://sproutsocial.com/insights/tiktok-algorithm/)
- [Buffer — TikTok Algorithm Guide 2026](https://buffer.com/resources/tiktok-algorithm/)
- [Whop Docs — Content Rewards](https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards)
- [Whop Blog — How to use Content Rewards on Whop](https://whop.com/blog/whop-content-rewards/)
- [Clippa — The Complete Whop Clipping Guide](https://clippa.net/blog/complete-whop-clipping-guide-2025/)
- [TikTok for Developers — Content Posting API: Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started)
- [TikTok for Developers — Content Posting API product page](https://developers.tiktok.com/products/content-posting-api/)
- [Zernio — TikTok posting API: limits, OAuth, and quick setup (2026)](https://zernio.com/blog/tiktok-posting-api)
- Berger, J. & Milkman, K. (2012). *What Makes Online Content Viral?* Journal of Marketing Research.
