"""Burned-in caption generation (ASS subtitles).

Big bold word-group captions in the TikTok style: 2-4 words per card,
white with heavy black outline, positioned in the lower-middle of the
frame; emphasis words highlighted in yellow. A separate top-positioned
"hook" title shows for the first three seconds — the retention window
that decides whether the clip lives or dies.
"""

from __future__ import annotations

import re
from pathlib import Path

from .segmenter import Sentence
from .virality import EMOTION_WORDS, MONEY_WORDS

_EMPHASIS = re.compile(
    "|".join([r"\$[\d,]+", r"\b\d+%"] + [re.escape(w) for w in EMOTION_WORDS if w.isalpha()]),
    re.I,
)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,7,2,5,60,60,660,1
Style: Hook,{font},72,&H0000E7FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,6,2,8,60,60,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _style_words(text: str) -> str:
    """Highlight emphasis words in yellow."""
    out_words = []
    for word in text.split():
        if _EMPHASIS.fullmatch(word.strip(".,!?")) or MONEY_WORDS.fullmatch(word.strip(".,!?") or ""):
            out_words.append(r"{\c&H00D7FF&}" + _escape(word) + r"{\c&HFFFFFF&}")
        else:
            out_words.append(_escape(word))
    return " ".join(out_words)


def _cards(sentence: Sentence, clip_start: float, words_per_card: int = 3):
    """Split a sentence into timed word-group cards. Caption segments only
    carry sentence-level timing, so time is allocated per word count."""
    words = sentence.text.split()
    if not words:
        return
    total = max(sentence.end - sentence.start, 0.3)
    per_word = total / len(words)
    for i in range(0, len(words), words_per_card):
        chunk = words[i:i + words_per_card]
        start = sentence.start - clip_start + i * per_word
        end = start + len(chunk) * per_word
        yield start, min(end, sentence.end - clip_start), " ".join(chunk)


def build_ass(sentences: list[Sentence], clip_start: float, clip_end: float,
              hook_text: str, font: str, out_path: Path) -> Path:
    """Write the .ass subtitle file for one clip."""
    lines = [ASS_HEADER.format(font=font)]

    if hook_text:
        hook = _escape(hook_text.upper())
        lines.append(
            f"Dialogue: 1,{_ts(0)},{_ts(min(3.0, clip_end - clip_start))},Hook,,0,0,0,,"
            r"{\fad(120,150)\bord6}" + hook + "\n"
        )

    duration = clip_end - clip_start
    for sentence in sentences:
        if sentence.end <= clip_start or sentence.start >= clip_end:
            continue
        for start, end, text in _cards(sentence, clip_start):
            start = max(0.0, start)
            end = min(duration, end)
            if end - start < 0.05:
                continue
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,"
                r"{\fad(60,40)}" + _style_words(text) + "\n"
            )

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
