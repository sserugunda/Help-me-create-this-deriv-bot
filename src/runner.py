import asyncio
import logging
from typing import Dict, Optional
from .deriv_client import DerivClient
from .strategy import BreakoutProbability, Candle
from .symbols import DERIV_VOLATILITY_SYMBOLS
from .config import settings

logger = logging.getLogger("Runner")

class BotRunner:
    def __init__(self) -> None:
        self.client = DerivClient()
        self.strategies: Dict[str, BreakoutProbability] = {}
        self.running: bool = False
        self.sub_ids: Dict[str, str] = {}
        self.symbols = list(DERIV_VOLATILITY_SYMBOLS)

    async def start(self) -> str:
        if self.running:
            return "Already running"
        self.running = True
        for sym in self.symbols:
            self.strategies[sym] = BreakoutProbability()
            sub_id = await self.client.subscribe_candles(sym, 60, self._make_handler(sym))
            self.sub_ids[sym] = sub_id
        return "Bot started"

    async def stop(self) -> str:
        if not self.running:
            return "Not running"
        for sym, sub_id in list(self.sub_ids.items()):
            try:
                await self.client.unsubscribe(sub_id)
            except Exception:
                pass
            self.sub_ids.pop(sym, None)
        self.running = False
        return "Bot stopped"

    async def status(self) -> str:
        bal = 0.0
        try:
            bal = await self.client.get_balance()
        except Exception:
            pass
        return (
            f"Running: {self.running}\n"
            f"Symbols: {','.join(self.symbols)}\n"
            f"ActiveSubs: {len(self.sub_ids)}\n"
            f"Stake%: {settings.stake_pct:.2f}\n"
            f"Balance: {bal:.2f}"
        )

    async def set_risk(self, pct: float) -> str:
        if pct <= 0 or pct > 10:
            return "Risk must be (0,10] percent"
        from .config import settings as cfg
        cfg.stake_pct = pct
        return f"Stake percent set to {pct:.2f}%"

    async def add_symbol(self, sym: str) -> str:
        sym = sym.strip().upper()
        if sym in self.symbols:
            return "Symbol already in list"
        self.symbols.append(sym)
        if self.running:
            self.strategies[sym] = BreakoutProbability()
            sub_id = await self.client.subscribe_candles(sym, 60, self._make_handler(sym))
            self.sub_ids[sym] = sub_id
        return f"Added {sym}"

    async def remove_symbol(self, sym: str) -> str:
        sym = sym.strip().upper()
        if sym not in self.symbols:
            return "Symbol not in list"
        self.symbols.remove(sym)
        sub_id = self.sub_ids.pop(sym, None)
        if sub_id:
            try:
                await self.client.unsubscribe(sub_id)
            except Exception:
                pass
        self.strategies.pop(sym, None)
        return f"Removed {sym}"

    def _make_handler(self, symbol: str):
        async def on_tick(msg):
            # Only act on closed candle
            ohlc = msg.get("ohlc")
            if not ohlc:
                return
            if ohlc.get("granularity") != 60:
                return
            if ohlc.get("is_closed") != 1:
                return
            candle = Candle(
                epoch=int(ohlc["open_time"]),
                open=float(ohlc["open"]),
                high=float(ohlc["high"]),
                low=float(ohlc["low"]),
                close=float(ohlc["close"]),
            )
            strat = self.strategies[symbol]
            signal = strat.on_candle(candle)
            if signal is None:
                return
            if signal:  # probability green > red
                try:
                    balance = await self.client.get_balance()
                    stake = max(0.35, round(balance * (settings.stake_pct / 100.0), 2))
                    await self.client.buy_rise(symbol, duration=1, stake=stake)
                    logger.info(f"Buy RISE {symbol} stake={stake}")
                except Exception as e:
                    logger.exception(f"Buy error {symbol}: {e}")
        return on_tick
