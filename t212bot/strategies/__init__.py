"""Strategy library: individual alpha models plus a weighted ensemble."""

from .base import Signal, Strategy
from .sma_crossover import SmaCrossover
from .rsi_reversion import RsiReversion
from .macd_momentum import MacdMomentum
from .bollinger_reversion import BollingerReversion
from .donchian_breakout import DonchianBreakout
from .ensemble import EnsembleStrategy, build_default_ensemble

__all__ = [
    "Signal",
    "Strategy",
    "SmaCrossover",
    "RsiReversion",
    "MacdMomentum",
    "BollingerReversion",
    "DonchianBreakout",
    "EnsembleStrategy",
    "build_default_ensemble",
]
