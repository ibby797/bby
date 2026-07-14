"""Audio-energy timeline via ffmpeg's ebur128 filter.

Loudness spikes correlate with laughter, shouting, and excitement — strong
signals for clip-worthy moments that a transcript alone can miss. One
audio-only decode pass produces a (t, momentary LUFS) timeline that the
virality scorer samples per candidate window.
"""

from __future__ import annotations

import logging
import re
import statistics
import subprocess
from bisect import bisect_left, bisect_right
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

_EBUR_RE = re.compile(r"t:\s*([0-9.]+)\s+.*?M:\s*(-?[0-9.]+|nan)", re.IGNORECASE)


class EnergyTimeline:
    def __init__(self, samples: list[tuple[float, float]]):
        # samples: sorted (time_seconds, momentary_lufs)
        self.times = [t for t, _ in samples]
        self.values = [v for _, v in samples]
        finite = [v for v in self.values if v > -70]
        self.baseline = statistics.median(finite) if finite else -23.0
        self.spread = statistics.pstdev(finite) if len(finite) > 1 else 3.0

    def window_stats(self, start: float, end: float) -> tuple[float, float]:
        """Return (mean_delta, peak_delta) in dB relative to the video baseline."""
        lo = bisect_left(self.times, start)
        hi = bisect_right(self.times, end)
        window = [v for v in self.values[lo:hi] if v > -70]
        if not window:
            return 0.0, 0.0
        return (statistics.fmean(window) - self.baseline, max(window) - self.baseline)


def analyze_energy(video_path: Path) -> EnergyTimeline | None:
    """Decode audio once and parse the ebur128 momentary-loudness log."""
    cmd = [
        settings.ffmpeg, "-hide_banner", "-nostats", "-i", str(video_path),
        "-map", "a:0?", "-filter:a", "ebur128", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("energy analysis skipped: %s", exc)
        return None

    samples: list[tuple[float, float]] = []
    for line in proc.stderr.splitlines():
        m = _EBUR_RE.search(line)
        if m and m.group(2) != "nan":
            samples.append((float(m.group(1)), float(m.group(2))))
    if len(samples) < 10:
        log.info("energy analysis produced too few samples (%d)", len(samples))
        return None
    return EnergyTimeline(samples)
