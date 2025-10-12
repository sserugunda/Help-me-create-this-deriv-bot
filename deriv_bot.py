import os
import asyncio
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import numpy as np
import websockets

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DERIV_ENDPOINT = os.getenv("DERIV_WS", "wss://ws.derivws.com/websockets/v3")
APP_ID = os.getenv("DERIV_APP_ID")
API_TOKEN = os.getenv("DERIV_API_TOKEN")

STAKE_AMOUNT = float(os.getenv("STAKE_AMOUNT", "0.5"))
ENTRY_SECOND_THRESHOLD = int(os.getenv("ENTRY_SECOND_THRESHOLD", "58"))
TRADE_DURATION_SECONDS = int(os.getenv("TRADE_DURATION_SECONDS", "55"))

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "2"))
EMA_PERIOD = int(os.getenv("EMA_PERIOD", "50"))
RSI_UPPER = float(os.getenv("RSI_UPPER", "55"))
RSI_LOWER = float(os.getenv("RSI_LOWER", "45"))

# Candle history sizes
CANDLES_1M_COUNT = int(os.getenv("CANDLES_1M_COUNT", "250"))
CANDLES_2M_COUNT = int(os.getenv("CANDLES_2M_COUNT", "150"))
CANDLES_3M_COUNT = int(os.getenv("CANDLES_3M_COUNT", "30"))

MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "13"))
SYMBOLS_CSV = os.getenv("SYMBOLS_CSV", "")

CURRENCY: Optional[str] = None

# ---------------- Indicator utilities ---------------- #

def compute_ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = alpha * v + (1.0 - alpha) * ema_val
    return float(ema_val)

def compute_rsi_wilder(values: List[float], period: int) -> Optional[float]:
    if len(values) < period + 1:
        return None
    deltas = np.diff(np.asarray(values, dtype=float))
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / (avg_loss if avg_loss != 0 else 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))

# ---------------- Websocket client ---------------- #

class DerivWSClient:
    def __init__(self, app_id: str, api_token: str):
        if not app_id or not api_token:
            raise RuntimeError("DERIV_APP_ID and DERIV_API_TOKEN must be set")
        self.app_id = app_id
        self.api_token = api_token
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.req_id_counter = 1

    async def connect(self):
        url = f"{DERIV_ENDPOINT}?app_id={self.app_id}"
        self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=20)
        await self.authorize()

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        assert self.ws is not None
        payload = dict(payload)
        payload["req_id"] = self.req_id_counter
        self.req_id_counter += 1
        await self.ws.send(json.dumps(payload))
        raw = await self.ws.recv()
        return json.loads(raw)

    async def authorize(self):
        global CURRENCY
        resp = await self._send({"authorize": self.api_token})
        if "error" in resp:
            raise RuntimeError(f"Authorize error: {resp['error']}")
        CURRENCY = resp.get("authorize", {}).get("currency") or CURRENCY or "USD"

    async def active_symbols(self) -> List[Dict[str, Any]]:
        resp = await self._send({"active_symbols": "brief", "product_type": "basic"})
        if "error" in resp:
            raise RuntimeError(f"active_symbols error: {resp['error']}")
        return resp.get("active_symbols", [])

    async def get_candles(self, symbol: str, granularity: int, count: int) -> List[Dict[str, Any]]:
        resp = await self._send({
            "ticks_history": symbol,
            "end": "latest",
            "style": "candles",
            "granularity": granularity,
            "count": count,
        })
        if "error" in resp:
            raise RuntimeError(f"ticks_history error for {symbol} g{granularity}: {resp['error']}")
        return resp.get("candles", [])

    async def proposal(self, symbol: str, contract_type: str, amount: float, duration_s: int) -> Dict[str, Any]:
        payload = {
            "proposal": 1,
            "amount": str(amount),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": CURRENCY or "USD",
            "duration": duration_s,
            "duration_unit": "s",
            "symbol": symbol,
        }
        resp = await self._send(payload)
        if "error" in resp:
            raise RuntimeError(f"proposal error: {resp['error']}")
        return resp["proposal"]

    async def buy(self, proposal_id: str, price: float) -> Dict[str, Any]:
        resp = await self._send({"buy": proposal_id, "price": price})
        if "error" in resp:
            raise RuntimeError(f"buy error: {resp['error']}")
        return resp["buy"]

    async def subscribe_open_contract(self, contract_id: int):
        assert self.ws is not None
        await self.ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1
        }))
        while True:
            raw = await self.ws.recv()
            data = json.loads(raw)
            if data.get("msg_type") == "proposal_open_contract":
                poc = data.get("proposal_open_contract", {})
                yield poc
                if poc.get("is_sold"):
                    break
            elif "error" in data:
                break

# ---------------- Symbol selection ---------------- #

async def select_volatility_symbols(client: DerivWSClient, limit: int) -> List[str]:
    symbols = await client.active_symbols()

    # Build lookup sets
    active_by_symbol = {s.get("symbol"): s for s in symbols if s.get("symbol")}
    active_symbols = set(active_by_symbol.keys())

    # If user provided explicit list, honor it with robust mapping
    if SYMBOLS_CSV.strip():
        requested = [x.strip() for x in SYMBOLS_CSV.split(',') if x.strip()]

        # Synonym candidates per requested code (ordered fallbacks)
        def candidates(code: str) -> List[str]:
            mapping: Dict[str, List[str]] = {
                # direct R without underscore
                "R10": ["R10", "R_10"],
                "R25": ["R25", "R_25"],
                "R50": ["R50", "R_50"],
                "R75": ["R75", "R_75"],
                "R100": ["R100", "R_100"],
                # 1HZ family to 1s streams and fallback to non-1s
                "1HZ10V": ["1HZ10V", "R_10_1s", "R_10"],
                "1HZ25V": ["1HZ25V", "R_25_1s", "R_25"],
                "1HZ50V": ["1HZ50V", "R_50_1s", "R_50"],
                "1HZ75V": ["1HZ75V", "R_75_1s", "R_75"],
                "1HZ100V": ["1HZ100V", "R_100_1s", "R_100"],
                # Unknowns: try direct first; if not present, no fallback
                "1HZ15V": ["1HZ15V"],
                "1HZ30V": ["1HZ30V"],
                "1HZ90V": ["1HZ90V"],
            }
            return mapping.get(code, [code])

        resolved: List[str] = []
        for code in requested:
            found: Optional[str] = None
            for cand in candidates(code):
                if cand in active_symbols:
                    found = cand
                    break
            if found is None:
                # attempt a loose match by display_name containing the number (best effort)
                number_part = ''.join(ch for ch in code if ch.isdigit())
                if number_part:
                    matches = [sym for sym, rec in active_by_symbol.items() if number_part in (rec.get("display_name") or "")]
                    if matches:
                        found = matches[0]
            if found and found not in resolved:
                resolved.append(found)

        # Enforce limit
        return resolved[:limit]

    # Default behavior: auto-select volatility indices, excluding ranges/boom/crash
    filtered: List[str] = []
    for s in symbols:
        if s.get("market") != "synthetic_index":
            continue
        display = (s.get("display_name") or "").lower()
        if "volatility" not in display:
            continue
        if any(x in display for x in ["step", "jump", "range break", "crash", "boom"]):
            continue
        sym = s.get("symbol")
        if sym:
            filtered.append(sym)
    # stable preference
    preferred = [
        "R_10", "R_25", "R_50", "R_75", "R_100",
        "R_10_1s", "R_25_1s", "R_50_1s", "R_75_1s", "R_100_1s",
        "R_150_1s", "R_200_1s", "R_300_1s",
    ]
    selected = [s for s in preferred if s in filtered][:limit]
    if len(selected) < limit:
        extras = [s for s in filtered if s not in selected]
        selected.extend(extras[: limit - len(selected)])
    return selected[:limit]

# ---------------- Strategy ---------------- #

def candle_color(open_price: float, close_price: float) -> str:
    return 'G' if close_price > open_price else 'R'

def evaluate_signal(closes_1m: List[float], closes_2m: List[float], candles_3m: List[Dict[str, Any]]) -> int:
    ema50 = compute_ema(closes_1m, EMA_PERIOD)
    rsi1 = compute_rsi_wilder(closes_1m, RSI_PERIOD)
    rsi2 = compute_rsi_wilder(closes_2m, RSI_PERIOD)

    if ema50 is None or rsi1 is None or rsi2 is None or len(candles_3m) < 2:
        return 0

    last_close = closes_1m[-1]
    c1, c2 = candles_3m[-1], candles_3m[-2]
    col1 = candle_color(float(c1["open"]), float(c1["close"]))
    col2 = candle_color(float(c2["open"]), float(c2["close"]))

    call_ok = (rsi1 > RSI_UPPER and rsi2 > RSI_UPPER and col1 == 'G' and col2 == 'G' and last_close > ema50)
    put_ok = (rsi1 < RSI_LOWER and rsi2 < RSI_LOWER and col1 == 'R' and col2 == 'R' and last_close < ema50)

    if call_ok:
        return 1
    if put_ok:
        return -1
    return 0

# ---------------- Workers ---------------- #

async def symbol_worker(symbol: str):
    backoff = 1.0
    while True:
        client = DerivWSClient(APP_ID, API_TOKEN)
        try:
            await client.connect()
            print(f"[{symbol}] Connected.")
            last_traded_minute: Optional[str] = None
            backoff = 1.0

            while True:
                now = datetime.now(timezone.utc)
                minute_key = now.strftime("%Y-%m-%d %H:%M")
                if now.second >= ENTRY_SECOND_THRESHOLD and last_traded_minute != minute_key:
                    try:
                        c1m = await client.get_candles(symbol, 60, CANDLES_1M_COUNT)
                        c2m = await client.get_candles(symbol, 120, CANDLES_2M_COUNT)
                        c3m = await client.get_candles(symbol, 180, CANDLES_3M_COUNT)
                    except Exception as e:
                        print(f"[{symbol}] Candle fetch error: {e}")
                        await asyncio.sleep(1.5)
                        continue

                    closes_1m = [float(c["close"]) for c in c1m]
                    closes_2m = [float(c["close"]) for c in c2m]
                    direction = evaluate_signal(closes_1m, closes_2m, c3m)

                    if direction != 0:
                        side = "CALL" if direction == 1 else "PUT"
                        try:
                            prop = await client.proposal(symbol, side, STAKE_AMOUNT, TRADE_DURATION_SECONDS)
                            proposal_id = prop["id"]
                            ask_price = float(prop["ask_price"])
                            print(f"[{symbol}] {side} signal. Buying @ {ask_price}")
                            buy_resp = await client.buy(proposal_id, ask_price)
                            contract_id = buy_resp["contract_id"]
                            async for poc in client.subscribe_open_contract(contract_id):
                                if poc.get("is_sold"):
                                    profit = float(poc.get("profit", 0.0))
                                    status = "WIN" if profit > 0 else ("TIE" if profit == 0 else "LOSS")
                                    print(f"[{symbol}] Settled {contract_id} -> {status} profit={profit:.2f}")
                                    break
                            last_traded_minute = minute_key
                        except Exception as e:
                            print(f"[{symbol}] Trade error: {e}")
                            last_traded_minute = minute_key
                    # else: no signal
                await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[{symbol}] Connection loop error: {e}; reconnecting in {backoff:.1f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)
        finally:
            try:
                await client.close()
            except Exception:
                pass

# ---------------- Orchestration ---------------- #

async def main():
    if not APP_ID or not API_TOKEN:
        print("Please set DERIV_APP_ID and DERIV_API_TOKEN (env or .env file)")
        raise SystemExit(1)

    bootstrap = DerivWSClient(APP_ID, API_TOKEN)
    await bootstrap.connect()
    symbols = await select_volatility_symbols(bootstrap, MAX_SYMBOLS)
    acct_cur = CURRENCY or "USD"
    await bootstrap.close()

    if not symbols:
        print("No eligible volatility symbols found.")
        raise SystemExit(1)

    print(f"Currency: {acct_cur}")
    print(f"Symbols ({len(symbols)}): {symbols}")

    tasks = [asyncio.create_task(symbol_worker(sym)) for sym in symbols]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
