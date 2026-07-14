"""Candidate window generation.

Transcript segments are grouped into sentence-ish units, then candidate
clip windows are generated from every sentence start at several target
lengths. The virality scorer ranks them and suppresses overlaps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .transcript import Segment

SENTENCE_END = re.compile(r"[.!?…]\s*$")


@dataclass
class Sentence:
    start: float
    end: float
    text: str


@dataclass
class Window:
    start: float
    end: float
    text: str
    sentences: list[Sentence]

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_sentences(segments: list[Segment]) -> list[Sentence]:
    """Merge raw caption segments into sentence units. Auto-generated
    captions rarely carry punctuation, so we also split on gaps and cap
    unit length."""
    sentences: list[Sentence] = []
    buf_text: list[str] = []
    buf_start: float | None = None
    last_end = 0.0

    def flush(end: float) -> None:
        nonlocal buf_text, buf_start
        if buf_text and buf_start is not None:
            text = " ".join(buf_text).strip()
            if text:
                sentences.append(Sentence(start=buf_start, end=end, text=text))
        buf_text, buf_start = [], None

    for seg in segments:
        gap = seg.start - last_end
        if buf_text and gap > 1.5:  # silence gap = natural boundary
            flush(last_end)
        if buf_start is None:
            buf_start = seg.start
        buf_text.append(seg.text)
        last_end = seg.end
        unit_len = last_end - buf_start
        if SENTENCE_END.search(seg.text) or unit_len >= 8.0:
            flush(last_end)
    flush(last_end)
    return sentences


def generate_windows(sentences: list[Sentence],
                     min_len: float, max_len: float,
                     targets: tuple[float, ...] = (22.0, 34.0, 50.0)) -> list[Window]:
    """From each sentence start, extend forward to each target length,
    always ending on a sentence boundary so clips feel self-contained."""
    windows: list[Window] = []
    n = len(sentences)
    for i in range(n):
        start = sentences[i].start
        for target in targets:
            if target < min_len or target > max_len + 10:
                continue
            chunk: list[Sentence] = []
            j = i
            while j < n:
                chunk.append(sentences[j])
                length = sentences[j].end - start
                if length >= target:
                    break
                j += 1
            if not chunk:
                continue
            end = chunk[-1].end
            duration = end - start
            if duration < min_len or duration > max_len:
                continue
            windows.append(Window(
                start=start, end=end,
                text=" ".join(s.text for s in chunk),
                sentences=chunk,
            ))
    # de-dup identical spans produced by different targets
    unique: dict[tuple[int, int], Window] = {}
    for w in windows:
        unique[(int(w.start * 10), int(w.end * 10))] = w
    return list(unique.values())
