import asyncio
import json
import logging
from typing import Any, Dict, Optional, Callable, Awaitable
import websockets
from .config import settings

DERIV_WSS = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"


class DerivClient:
    def __init__(self) -> None:
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._send_lock = asyncio.Lock()
        self._logger = logging.getLogger("DerivClient")

        # Single receiver pump state
        self._receiver_task: Optional[asyncio.Task[None]] = None
        self._pending_requests: dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self._next_req_id: int = 1

        # Candle subscriptions
        self._candles_handlers: dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {}
        self._symbol_to_subid: dict[str, str] = {}
        self._subid_to_symbol: dict[str, str] = {}
        self._pending_subscribe: dict[str, asyncio.Future[str]] = {}

    async def connect(self) -> None:
        if self._ws and not self._ws.closed:
            return
        uri = DERIV_WSS.format(app_id=settings.deriv_app_id)
        self._ws = await websockets.connect(uri, ping_interval=20, ping_timeout=20)
        # Start receiver before authorize so we can resolve via req_id
        self._receiver_task = asyncio.create_task(self._receiver_loop())
        await self._authorize()

    async def _receiver_loop(self) -> None:
        assert self._ws
        try:
            while True:
                msg = await self._ws.recv()
                data = json.loads(msg)
                # Resolve request futures by req_id first
                req_id = data.get("req_id")
                if isinstance(req_id, int) and req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(data)
                    continue

                msg_type = data.get("msg_type")
                if msg_type == "ohlc":
                    ohlc = data.get("ohlc", {})
                    symbol = ohlc.get("symbol")
                    sub_id = (data.get("subscription") or {}).get("id")
                    if symbol and sub_id:
                        # Map sub id to symbol if first time
                        if symbol not in self._symbol_to_subid:
                            self._symbol_to_subid[symbol] = sub_id
                            self._subid_to_symbol[sub_id] = symbol
                            # Complete pending subscribe future
                            fut = self._pending_subscribe.pop(symbol, None)
                            if fut and not fut.done():
                                fut.set_result(sub_id)
                        # Dispatch to handler
                        handler = self._candles_handlers.get(symbol)
                        if handler:
                            try:
                                await handler(data)
                            except Exception as e:
                                self._logger.exception(f"Handler error for {symbol}: {e}")
                    continue

                # Other streamed messages can be handled here if needed
        except websockets.ConnectionClosed:
            pass
        except Exception:
            self._logger.exception("Receiver loop crashed")

    async def _send(self, payload: Dict[str, Any]) -> int:
        # Attach unique req_id for correlation
        req_id = self._next_req_id
        self._next_req_id += 1
        payload = dict(payload)
        payload["req_id"] = req_id
        async with self._send_lock:
            assert self._ws
            await self._ws.send(json.dumps(payload))
        return req_id

    async def request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await self.connect()
        req_id = await self._send(payload)
        fut: asyncio.Future[Dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending_requests[req_id] = fut
        data = await fut
        # Deriv errors are posted inline
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    async def _authorize(self) -> None:
        data = await self.request({"authorize": settings.deriv_api_token})
        if data.get("msg_type") != "authorize":
            raise RuntimeError("Authorization failed")

    async def subscribe_candles(
        self, symbol: str, granularity: int, on_tick: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> str:
        await self.connect()
        # Register handler and a future to capture first ohlc with sub id
        self._candles_handlers[symbol] = on_tick
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_subscribe[symbol] = fut
        await self._send(
            {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": 100,
                "granularity": granularity,
                "style": "candles",
                "subscribe": 1,
            }
        )
        # Optional: we could also wait for the historical "candles" reply via req_id, but not required
        sub_id = await fut
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        await self.request({"forget": sub_id})
        symbol = self._subid_to_symbol.pop(sub_id, None)
        if symbol:
            self._symbol_to_subid.pop(symbol, None)
            self._candles_handlers.pop(symbol, None)
            self._pending_subscribe.pop(symbol, None)

    async def get_balance(self) -> float:
        data = await self.request({"balance": 1, "account": settings.deriv_account_id})
        return float(data["balance"]["balance"])  # type: ignore

    async def buy_rise(self, symbol: str, duration: int, stake: float) -> Dict[str, Any]:
        proposal = await self.request(
            {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": "CALL",
                "currency": "USD",
                "duration": duration,
                "duration_unit": "m",
                "symbol": symbol,
            }
        )
        proposal_id = proposal.get("proposal", {}).get("id")
        if not proposal_id:
            raise RuntimeError("No proposal id returned")
        buy = await self.request({"buy": proposal_id, "price": stake})
        return buy

    async def close(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()  # type: ignore
        self._ws = None
        if self._receiver_task:
            self._receiver_task.cancel()
            import contextlib
            with contextlib.suppress(Exception):
                await self._receiver_task
            self._receiver_task = None
