# 🎬 ClipFarm

Paste a long-form YouTube video → ClipFarm finds the viral moments, cuts
them into up to **100 vertical (9:16) clips with burned-in captions and
hook titles**, and drip-posts them to TikTok on a schedule — built for
**content-rewards clipping** (Whop Content Rewards and similar pay-per-view
campaigns).

```
YouTube URL ──▶ download ──▶ transcript + audio-energy analysis
            ──▶ virality scoring (research-based, see docs/VIRAL_RESEARCH.md)
            ──▶ batch render 9:16 clips (captions, hook title, loudness-normalized)
            ──▶ campaign manifest (CSV) + TikTok automation queue
```

## Features

- **Viral moment detection** — transcript-based scoring engine built on
  documented virality drivers: 3-second hook strength, completion-rate
  sweet spot (20–35s), high-arousal emotion, curiosity gap + payoff,
  audio-energy spikes (laughter/excitement via ffmpeg `ebur128`), and
  share triggers (money/controversy/relatability). Full research +
  weights: [`docs/VIRAL_RESEARCH.md`](docs/VIRAL_RESEARCH.md).
- **Optional AI re-rank** — set `ANTHROPIC_API_KEY` and the top candidates
  are re-scored by Claude, which also writes punchier on-screen hook titles.
- **Auto-editing** — 1080×1920 output with blurred-pad or center-crop
  framing, TikTok-style word-group captions (emphasis words highlighted),
  a 3-second hook title overlay, watermark, and −14 LUFS loudness
  normalization. Up to 100 clips per video, rendered concurrently.
- **Content-rewards workflow** — campaign settings (rate per 1k views,
  min/max clip length, caption template, required @mention) pre-filter
  ineligible clips and generate a `submissions.csv` manifest for payout
  tracking.
- **TikTok automation** — official Content Posting API integration
  (OAuth, chunked upload, direct-post or inbox/draft mode) with a
  scheduler that drip-posts N clips/day inside a posting window with
  jitter. No API app? It runs in **export mode**: clips are surfaced as
  "ready to post" on the same schedule for manual upload.
- **Autopilot** — one click/one command runs the whole chain: analyze →
  render top N → queue → manifest.

## Quick start

```bash
cd clipfarm
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# ffmpeg is required for rendering/analysis
# macOS: brew install ffmpeg   |   Debian/Ubuntu: sudo apt install ffmpeg

python -m clipfarm serve       # open http://localhost:8000
```

Headless autopilot:

```bash
python -m clipfarm autopilot "https://www.youtube.com/watch?v=..." --clips 100 --watermark "@yourhandle"
```

## Configuration

Everything works with zero config. Optional environment variables (or a
`clipfarm.yaml` next to where you run it):

| Variable | Default | Purpose |
|---|---|---|
| `CLIPFARM_DATA` | `data` | storage directory |
| `CLIPFARM_MAX_CLIPS` | `100` | max clips per video |
| `CLIPFARM_MIN_CLIP` / `CLIPFARM_MAX_CLIP` | `12` / `58` | clip length bounds (s) |
| `CLIPFARM_RENDER_CONCURRENCY` | `2` | parallel ffmpeg renders |
| `CLIPFARM_FONT` | `DejaVu Sans` | caption font |
| `ANTHROPIC_API_KEY` | — | enables Claude re-ranking + AI hook titles (`pip install anthropic`) |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | — | TikTok developer app credentials |
| `TIKTOK_POST_MODE` | `inbox` | `inbox` (drafts) or `direct` |
| `CLIPFARM_POSTS_PER_DAY` | `6` | automation cadence (hard cap 25 = TikTok API limit) |
| `CLIPFARM_POST_WINDOW_START/END` | `9` / `23` | local posting window (hours) |

Videos without captions: install `faster-whisper` for local
transcription, otherwise ClipFarm falls back to audio-energy-only
moment detection.

## Deploy as a website (hosted)

ClipFarm ships with a Docker image (ffmpeg included) and is ready for any
host. **Always set `CLIPFARM_PASSWORD`** when exposing it to the internet —
the entire UI/API is then gated behind a login prompt (username `clipfarm`).

**Option A — any VPS (Hetzner/DigitalOcean/etc., ~$5–10/mo, most reliable):**

```bash
git clone https://github.com/ibby797/bby.git && cd bby/clipfarm
CLIPFARM_PASSWORD=yourpassword docker compose up -d --build
# → website live at http://your-server-ip:8000
```

Put Caddy or nginx in front for HTTPS + a domain.

**Option B — Render.com:** New + → *Blueprint* → select this repo. The
root-level `render.yaml` builds ClipFarm with a persistent disk; you'll be
prompted for `CLIPFARM_PASSWORD`. Needs a paid instance (video rendering
requires disk + no sleep).

**Option C — Fly.io:** see the comments in [`fly.toml`](fly.toml) — four
commands and you're live with a volume and HTTPS.

**Cloud caveat — YouTube bot detection:** YouTube often rate-limits or
blocks downloads from datacenter IPs. If downloads fail on your host,
export your browser's YouTube cookies to a `cookies.txt` (any
"cookies.txt" browser extension) and set `CLIPFARM_COOKIES=/data/cookies.txt`
(mount the file there). Running on a home server/VPS with a residential
IP avoids this entirely.

For hosted TikTok posting set
`TIKTOK_REDIRECT_URI=https://your-domain.com/api/tiktok/callback` and add
the same URL in your TikTok developer app settings.

## TikTok automation — read this

ClipFarm uses TikTok's **official Content Posting API**, the only
ToS-compliant way to automate posting:

1. Create an app at [developers.tiktok.com](https://developers.tiktok.com),
   add the **Content Posting API** product, request `video.upload` +
   `video.publish` scopes, and set the redirect URI to
   `http://localhost:8000/api/tiktok/callback`.
2. Export `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`, restart, click
   **Connect TikTok**.
3. **Unaudited apps**: TikTok restricts direct posts to private
   (SELF_ONLY) visibility until your app passes their audit. Until then
   use the default `inbox` mode — clips land in your TikTok drafts and
   you tap Post in the app (still ~10 seconds per clip instead of
   editing for 20 minutes). After audit approval, switch
   `TIKTOK_POST_MODE=direct` for full hands-off publishing.
4. API limit: 25 posts/account/day — the scheduler respects this.

Unofficial posting bots violate TikTok's ToS and get accounts banned —
ClipFarm deliberately doesn't do that.

## Content-rewards checklist

- Set the campaign's **rate**, **min/max duration**, **caption template**
  (`{hook}` is replaced by the clip's hook line), and any **required
  @mention** in the ⚙ Campaign card — ineligible clips are skipped at
  queue time so you don't submit rejectable clips.
- Only clip source videos the campaign covers — brands approve or reject
  each submission, and rights to clip come from the campaign itself.
- Export `submissions.csv` per batch and fill in posted URLs/views to
  track estimated earnings against the campaign rate.

## Layout

```
clipfarm/
  clipfarm/
    config.py            settings (env + clipfarm.yaml)
    jobs.py              job state machine (analyze/render)
    server.py            FastAPI app + REST API
    pipeline/
      ingest.py          yt-dlp download + ffprobe
      transcript.py      YouTube captions → whisper fallback
      audio.py           ffmpeg ebur128 loudness timeline
      segmenter.py       sentence building + candidate windows
      virality.py        scoring engine (the interesting part)
      ai_scorer.py       optional Claude re-ranking
      captions.py        ASS subtitle generation
      renderer.py        ffmpeg 9:16 render pipeline
    rewards/campaigns.py campaign rules + submissions manifest
    tiktok/client.py     Content Posting API (OAuth/upload/post)
    tiktok/scheduler.py  drip-posting automation queue
  web/                   dashboard (vanilla JS)
  docs/VIRAL_RESEARCH.md research → feature mapping + sources
  tests/                 scoring engine tests
```
