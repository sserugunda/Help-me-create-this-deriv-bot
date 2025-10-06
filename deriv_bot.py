#!/usr/bin/env python3
import os
import json
import asyncio
import argparse
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any, Deque
from collections import deque

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = lambda: None  # no-op if dotenv is not available


@dataclass
class Candle:
    start_epoch: int
    end_epoch: int
    open: float
    high: float
    low: float
    close: float

    @property
    def color(self) -> int:
        return 1 if self.close > self.open else 0

    @property
    def midpoint(self) -> float:
        return (self.open + self.close) / 2.0


class CandleAggregator:
    def __init__(self, granularity_seconds: int) -> None:
        self.granularity_seconds = granularity_seconds
        self.current_bucket_start: Optional[int] = None
        self.current_open: Optional[float] = None
        self.current_high: Optional[float] = None
        self.current_low: Optional[float] = None
        self.current_close: Optional[float] = None

    def process_tick(self, epoch: int, price: float) -> Optional[Candle]:
        bucket_start = epoch - (epoch % self.granularity_seconds)
        if self.current_bucket_start is None:
            self.current_bucket_start = bucket_start
            self.current_open = price
            self.current_high = price
            self.current_low = price
            self.current_close = price
            return None

        if bucket_start == self.current_bucket_start:
            if price > float(self.current_high):
                self.current_high = price
            if price < float(self.current_low):
                self.current_low = price
            self.current_close = price
            return None

        # Finalize previous candle when bucket changes
        closed_candle = Candle(
            start_epoch=int(self.current_bucket_start),
            end_epoch=int(self.current_bucket_start + self.granularity_seconds - 1),
            open=float(self.current_open),
            high=float(self.current_high),
            low=float(self.current_low),
            close=float(self.current_close),
        )

        # Initialize new bucket with current tick
        self.current_bucket_start = bucket_start
        self.current_open = price
        self.current_high = price
        self.current_low = price
        self.current_close = price

        return closed_candle


class DerivClient:
    def __init__(self, app_id: str, api_token: str, endpoint: Optional[str] = None) -> None:
        self.app_id = app_id
        self.api_token = api_token
        self.endpoint = endpoint or f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        self.ws = None  # type: ignore
        self._req_id = 1000
        self._pending: Dict[int, asyncio.Future] = {}
        self._subscriptions: Dict[str, Callable[[dict], None]] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._connected = False

    async def connect_and_authorize(self) -> None:
        import websockets  # lazy import to avoid dependency if not used

        self.ws = await websockets.connect(self.endpoint, max_size=2**23)
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader())

        resp = await self.request({"authorize": self.api_token})
        if resp.get("error"):
            raise RuntimeError(f"Authorization failed: {resp['error']}")
        account = resp.get("authorize", {}).get("loginid", "?")
        currency = resp.get("authorize", {}).get("currency", "USD")
        print(f"Authorized on account {account} (currency={currency})")

    async def close(self) -> None:
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

    async def _reader(self) -> None:
        try:
            while self._connected and self.ws:
                raw = await self.ws.recv()
                msg = json.loads(raw)

                # Dispatch subscription updates first
                sub = msg.get("subscription")
                if sub and isinstance(sub, dict):
                    sub_id = sub.get("id")
                    if sub_id and sub_id in self._subscriptions:
                        try:
                            self._subscriptions[sub_id](msg)
                        except Exception as e:
                            print(f"Subscription handler error: {e}")

                # Resolve pending request futures by req_id
                req_id = msg.get("req_id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"Reader error: {e}")

    async def request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._req_id += 1
        payload["req_id"] = self._req_id
        assert self.ws is not None
        await self.ws.send(json.dumps(payload))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[self._req_id] = fut
        resp = await fut
        if resp.get("error"):
            return resp
        return resp

    async def subscribe_ticks(self, symbol: str, on_tick: Callable[[int, float], None]) -> str:
        resp = await self.request({"ticks": symbol, "subscribe": 1})
        if resp.get("error"):
            raise RuntimeError(f"Tick subscription error: {resp['error']}")
        sub_id = resp.get("subscription", {}).get("id")
        if not sub_id:
            # Some responses include subscription id under tick.id
            sub_id = resp.get("tick", {}).get("id")
        if not sub_id:
            raise RuntimeError("Could not obtain subscription id for ticks")

        def handler(msg: dict) -> None:
            tick = msg.get("tick")
            if not tick:
                return
            epoch = int(tick.get("epoch"))
            price = float(tick.get("quote"))
            on_tick(epoch, price)

        self._subscriptions[sub_id] = handler
        print(f"Subscribed to ticks for {symbol} (sub_id={sub_id})")
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        resp = await self.request({"forget": sub_id})
        if resp.get("error"):
            print(f"Unsubscribe error: {resp['error']}")
        self._subscriptions.pop(sub_id, None)

    async def subscribe_open_contract(self, contract_id: int, on_update: Callable[[dict], None]) -> str:
        resp = await self.request({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1})
        if resp.get("error"):
            raise RuntimeError(f"Open contract subscribe error: {resp['error']}")
        sub_id = resp.get("subscription", {}).get("id")
        if not sub_id:
            raise RuntimeError("No subscription id for open contract")

        def handler(msg: dict) -> None:
            poc = msg.get("proposal_open_contract")
            if not poc:
                return
            on_update(poc)

        self._subscriptions[sub_id] = handler
        return sub_id

    async def get_proposal(self, *, symbol: str, contract_type: str, stake: float, duration_minutes: int, currency: str = "USD") -> Dict[str, Any]:
        payload = {
            "proposal": 1,
            "amount": float(stake),
            "basis": "stake",
            "contract_type": contract_type,  # "CALL" or "PUT"
            "duration": int(duration_minutes),
            "duration_unit": "m",
            "currency": currency,
            "symbol": symbol,
        }
        resp = await self.request(payload)
        return resp

    async def buy(self, proposal_id: str, price: float) -> Dict[str, Any]:
        resp = await self.request({"buy": 1, "price": float(price), "proposal_id": proposal_id})
        return resp


class StrategyRunner:
    def __init__(
        self,
        client: DerivClient,
        *,
        symbol: str,
        n_streak: int,
        expiry_minutes: int,
        stake: float,
        currency: str,
        dry_run: bool,
    ) -> None:
        self.client = client
        self.symbol = symbol
        self.n_streak = max(1, int(n_streak))
        self.expiry_minutes = max(1, int(expiry_minutes))
        self.stake = float(stake)
        self.currency = currency
        self.dry_run = dry_run

        self.agg_1m = CandleAggregator(60)
        self.agg_5m = CandleAggregator(300)
        self.last_5m_candle: Optional[Candle] = None
        self.last_n_colors: Deque[int] = deque(maxlen=self.n_streak)

        self._open_contract_monitors: Dict[int, str] = {}  # contract_id -> sub_id

    def _on_tick(self, epoch: int, price: float) -> None:
        closed_1m = self.agg_1m.process_tick(epoch, price)
        closed_5m = self.agg_5m.process_tick(epoch, price)

        if closed_5m is not None:
            self.last_5m_candle = closed_5m
            print(
                f"[5m] {closed_5m.start_epoch}-{closed_5m.end_epoch} O={closed_5m.open:.5f} C={closed_5m.close:.5f}"
            )

        if closed_1m is not None:
            self._on_1m_candle_close(closed_1m)

    def _on_1m_candle_close(self, candle: Candle) -> None:
        self.last_n_colors.append(candle.color)
        print(
            f"[1m] {candle.start_epoch}-{candle.end_epoch} O={candle.open:.5f} C={candle.close:.5f} color={candle.color}"
        )

        if len(self.last_n_colors) < self.n_streak or not self.last_5m_candle:
            return

        is_green_streak = all(c == 1 for c in self.last_n_colors)
        is_red_streak = all(c == 0 for c in self.last_n_colors)

        is_green_aligned = self.last_5m_candle.color == 1
        is_red_aligned = self.last_5m_candle.color == 0

        midpoint_5m = self.last_5m_candle.midpoint
        is_pa_confirmed = False

        if is_green_aligned:
            is_pa_confirmed = candle.close < midpoint_5m
        elif is_red_aligned:
            is_pa_confirmed = candle.close > midpoint_5m

        trade_type: Optional[str] = None
        if is_green_streak and is_green_aligned and is_pa_confirmed:
            trade_type = "CALL"
        elif is_red_streak and is_red_aligned and is_pa_confirmed:
            trade_type = "PUT"

        if trade_type:
            asyncio.create_task(self._execute_trade(trade_type, candle))

    async def _execute_trade(self, trade_type: str, candle: Candle) -> None:
        print(
            f"Signal: {trade_type} at 1m close={candle.close:.5f}, 5m midpoint={self.last_5m_candle.midpoint:.5f}"
        )
        if self.dry_run:
            print("Dry-run mode: not placing order.")
            return

        # Get quote (proposal)
        prop = await self.client.get_proposal(
            symbol=self.symbol,
            contract_type=trade_type,
            stake=self.stake,
            duration_minutes=self.expiry_minutes,
            currency=self.currency,
        )
        if prop.get("error"):
            print(f"Proposal error: {prop['error']}")
            return

        proposal = prop.get("proposal") or {}
        proposal_id = proposal.get("id")
        ask_price = float(proposal.get("ask_price", self.stake))
        if not proposal_id:
            print("No proposal id returned; skipping buy.")
            return

        # Place order
        buy_resp = await self.client.buy(proposal_id, price=ask_price)
        if buy_resp.get("error"):
            print(f"Buy error: {buy_resp['error']}")
            return

        buy_info = buy_resp.get("buy", {})
        contract_id = buy_info.get("contract_id")
        buy_price = float(buy_info.get("buy_price", ask_price))
        print(f"Bought {trade_type} {self.symbol} id={contract_id} price={buy_price:.2f} for {self.expiry_minutes}m")

        if not contract_id:
            return

        # Monitor contract until sold
        async def on_update(poc: dict) -> None:
            is_sold = bool(poc.get("is_sold"))
            final_status = poc.get("status")
            current_spot = poc.get("current_spot")
            entry_spot = poc.get("entry_spot" )
            profit = poc.get("profit")
            if is_sold:
                print(
                    f"Contract {contract_id} settled: status={final_status} profit={profit} entry={entry_spot} spot={current_spot}"
                )

        sub_id = await self.client.subscribe_open_contract(int(contract_id), lambda poc: asyncio.create_task(on_update(poc)))
        self._open_contract_monitors[int(contract_id)] = sub_id

    async def run(self) -> None:
        sub_id: Optional[str] = None
        try:
            # Discover currency from authorize response would be ideal; default to self.currency
            sub_id = await self.client.subscribe_ticks(self.symbol, self._on_tick)
            print("Running. Press Ctrl+C to stop.")
            while True:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            if sub_id:
                try:
                    await self.client.unsubscribe(sub_id)
                except Exception:
                    pass


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv()

    app_id = args.app_id or os.getenv("DERIV_APP_ID") or "1089"  # demo app id; replace with your own for live
    token = args.token or os.getenv("DERIV_API_TOKEN")
    if not token and not args.dry_run:
        raise SystemExit("Missing API token. Use --token or set DERIV_API_TOKEN. Or use --dry-run.")

    endpoint = args.endpoint or os.getenv("DERIV_ENDPOINT")  # optional override

    client = DerivClient(app_id=app_id, api_token=token or "", endpoint=endpoint)
    await client.connect_and_authorize()

    runner = StrategyRunner(
        client,
        symbol=args.symbol,
        n_streak=args.n_streak,
        expiry_minutes=args.expiry_minutes,
        stake=args.stake,
        currency=args.currency or "USD",
        dry_run=args.dry_run,
    )

    try:
        await runner.run()
    finally:
        await client.close()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deriv bot: Streak + 5m alignment + PA (no ATR)")
    p.add_argument("--symbol", type=str, default="R_100", help="Deriv symbol, e.g. R_100, FRXEURUSD")
    p.add_argument("--app-id", type=str, default=None, help="Deriv app_id (or env DERIV_APP_ID)")
    p.add_argument("--token", type=str, default=None, help="Deriv API token (or env DERIV_API_TOKEN)")
    p.add_argument("--endpoint", type=str, default=None, help="Override WebSocket endpoint URL")
    p.add_argument("--n-streak", type=int, default=1, help="Number of consecutive 1m candles of same color")
    p.add_argument("--expiry-minutes", type=int, default=3, help="Contract duration in minutes")
    p.add_argument("--stake", type=float, default=1.0, help="Stake amount per trade")
    p.add_argument("--currency", type=str, default=None, help="Trading currency (default from account)")
    p.add_argument("--dry-run", action="store_true", help="Run without placing orders")
    return p


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Graceful shutdown on Ctrl+C
    stop_event = asyncio.Event()

    def _handle_sigint():
        if not stop_event.is_set():
            stop_event.set()

    try:
        import signal as _signal
        loop.add_signal_handler(_signal.SIGINT, _handle_sigint)
        loop.add_signal_handler(_signal.SIGTERM, _handle_sigint)
    except NotImplementedError:
        pass

    async def runner():
        task = asyncio.create_task(main_async(args))
        await stop_event.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(runner())


if __name__ == "__main__":
    main()
