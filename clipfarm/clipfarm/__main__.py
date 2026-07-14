"""CLI entry points.

  python -m clipfarm serve                 # start the web app on :8000
  python -m clipfarm autopilot <url>       # headless: analyze + render + queue
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="clipfarm")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    auto = sub.add_parser("autopilot", help="analyze a video, render top clips, queue them")
    auto.add_argument("url")
    auto.add_argument("--clips", type=int, default=100)
    auto.add_argument("--mode", default="blur", choices=["blur", "crop"])
    auto.add_argument("--watermark", default="")

    args = parser.parse_args()

    if args.command == "serve" or args.command is None:
        import uvicorn
        uvicorn.run("clipfarm.server:app", host=getattr(args, "host", "127.0.0.1"),
                    port=getattr(args, "port", 8000))
    elif args.command == "autopilot":
        asyncio.run(_autopilot(args))
    else:
        parser.print_help()
        sys.exit(1)


async def _autopilot(args) -> None:
    from .config import settings
    from .jobs import Job, run_analysis, run_render
    from .pipeline.renderer import RenderStyle
    from .rewards.campaigns import CampaignStore, export_manifest

    if not settings.ffmpeg_available():
        sys.exit("ffmpeg not found — install ffmpeg first")

    job = Job(url=args.url)
    job.save()
    print(f"job {job.id}: analyzing {args.url}")
    await run_analysis(job)
    job = Job.load(job.id)
    if job.status != "ready":
        sys.exit(f"analysis failed: {job.error}")
    print(f"job {job.id}: {len(job.moments)} moments found; rendering top {args.clips}")

    count = min(args.clips, len(job.moments))
    await run_render(job, list(range(count)),
                     RenderStyle(mode=args.mode, watermark=args.watermark))
    job = Job.load(job.id)
    ok = [c for c in job.clips if c.get("ok")]
    print(f"job {job.id}: rendered {len(ok)}/{count} clips → {job.clips_dir}")

    store = CampaignStore(settings.data_dir)
    campaign = store.load()[0]
    clips = [{**c, "caption": campaign.build_caption(c.get("hook", ""))} for c in ok]
    export_manifest(clips, campaign, job.dir)
    print(f"manifest: {job.dir / 'submissions.csv'}")


if __name__ == "__main__":
    main()
