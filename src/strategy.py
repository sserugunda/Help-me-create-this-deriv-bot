from __future__ import annotations
from dataclasses import dataclass
from typing import Deque, Optional
from collections import deque

@dataclass
class Candle:
    epoch: int
    open: float
    high: float
    low: float
    close: float

class BreakoutProbability:
    def __init__(self, max_bars: int = 5000) -> None:
        self.candles: Deque[Candle] = deque(maxlen=max_bars)
        self.green_count: int = 0
        self.red_count: int = 0

    def on_candle(self, c: Candle) -> Optional[bool]:
        # Assumes candles come in order and are final close candles
        if self.candles:
            prev = self.candles[-1]
            was_green = prev.close > prev.open
            was_red = prev.close < prev.open
            if was_green:
                self.green_count += 1
            if was_red:
                self.red_count += 1
        self.candles.append(c)
        # Decision is for the upcoming bar based on historical green/red counts
        total = self.green_count + self.red_count
        if total == 0:
            return None
        prob_green = self.green_count / total
        prob_red = self.red_count / total
        return prob_green > prob_red
