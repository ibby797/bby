"""Persistent bot state (JSON on disk).

Tracks what the exchange cannot: software-managed stop/take-profit levels,
the equity peak for drawdown control, and the day's starting equity for the
daily-loss kill switch. Written atomically so a crash never corrupts it.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ManagedPosition:
    ticker: str                 # broker symbol
    yahoo_symbol: str
    quantity: float             # always positive; see direction
    entry_price: float
    entry_time: str             # ISO timestamp
    stop_price: float
    take_profit_price: float
    highest_close: float        # trailing-stop extreme: highest close since
                                # entry for longs, LOWEST close for shorts
    atr_at_entry: float
    direction: int = 1          # +1 long, -1 short

    @classmethod
    def from_dict(cls, d: dict) -> "ManagedPosition":
        # tolerate states written by older versions (missing new fields)
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class BotState:
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    cooldowns: dict[str, str] = field(default_factory=dict)  # ticker -> ISO exit time
    peak_equity: float = 0.0
    day_date: str = ""           # YYYY-MM-DD the day_start_equity belongs to
    day_start_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "cooldowns": dict(self.cooldowns),
            "peak_equity": self.peak_equity,
            "day_date": self.day_date,
            "day_start_equity": self.day_start_equity,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        state = cls(
            cooldowns={str(k): str(v) for k, v in (d.get("cooldowns") or {}).items()},
            peak_equity=float(d.get("peak_equity", 0.0)),
            day_date=str(d.get("day_date", "")),
            day_start_equity=float(d.get("day_start_equity", 0.0)),
            halted=bool(d.get("halted", False)),
            halt_reason=str(d.get("halt_reason", "")),
        )
        for ticker, pos in (d.get("positions") or {}).items():
            state.positions[ticker] = ManagedPosition.from_dict(pos)
        return state


def load_state(path: str | Path) -> BotState:
    p = Path(path)
    if not p.exists():
        return BotState()
    with open(p) as fh:
        return BotState.from_dict(json.load(fh))


def save_state(state: BotState, path: str | Path) -> None:
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent) or ".", prefix=".state_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state.to_dict(), fh, indent=2)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
