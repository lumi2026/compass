"""Live Kalshi listener: websocket topics with REST fallback.

Contract: `handle_message` is pure (message in, updates out) so the
protocol logic is testable without a network. `KalshiListener` owns the
socket, reconnects with backoff, and falls back to REST polling when no
API keys are configured (the socket handshake requires auth even for
public channels).
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sources.kalshi import OrderBookSnapshot, PriceLevel, _parse_levels

from .kalshi_auth import WS_PATH, WS_URLS, Credentials, auth_headers

CHANNELS = ["ticker", "orderbook_delta", "trade"]


@dataclass(frozen=True)
class Update:
    kind: str  # ticker | orderbook | trade
    ticker: str
    payload: dict[str, Any]
    ts_ms: int = 0


@dataclass
class TickerState:
    ticker: str
    last: dict[str, Any] = field(default_factory=dict)
    orderbook: OrderBookSnapshot | None = None
    updated_ms: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def apply_delta(book: OrderBookSnapshot, side: str, price_cents: int, count: int) -> OrderBookSnapshot:
    """Contract: delta sets the level; count 0 removes it; sides stay sorted."""
    levels = list(book.yes_bids if side == "yes" else book.no_bids)
    levels = [lv for lv in levels if lv.price_cents != price_cents]
    if count > 0:
        levels.append(PriceLevel(price_cents=price_cents, contracts=count))
    levels.sort(key=lambda lv: -lv.price_cents)
    if side == "yes":
        return OrderBookSnapshot(ticker=book.ticker, yes_bids=levels, no_bids=book.no_bids)
    return OrderBookSnapshot(ticker=book.ticker, yes_bids=book.yes_bids, no_bids=levels)


def handle_message(msg: dict, state: dict[str, TickerState]) -> list[Update]:
    """Contract: pure handler. Mutates `state`, returns updates to publish."""
    out: list[Update] = []
    mtype = msg.get("type")
    body = msg.get("msg") or {}
    ticker = str(body.get("market_ticker") or body.get("ticker") or "")
    if not ticker:
        return out
    st = state.setdefault(ticker, TickerState(ticker=ticker))

    if mtype == "ticker":
        st.last = body
        st.updated_ms = int(msg.get("sending_ts_ms") or _now_ms())
        out.append(Update(kind="ticker", ticker=ticker, payload=body, ts_ms=st.updated_ms))
    elif mtype == "orderbook_snapshot":
        book = OrderBookSnapshot(
            ticker=ticker,
            yes_bids=_parse_levels(body.get("yes_dollars_fp", body.get("yes", []))),
            no_bids=_parse_levels(body.get("no_dollars_fp", body.get("no", []))),
        )
        st.orderbook = book
        st.updated_ms = _now_ms()
        out.append(Update(kind="orderbook", ticker=ticker, payload={"levels": "snapshot"}, ts_ms=st.updated_ms))
    elif mtype == "orderbook_delta":
        if st.orderbook is None:
            st.orderbook = OrderBookSnapshot(ticker=ticker, yes_bids=[], no_bids=[])
        from sources.kalshi import _to_cents, _to_contracts

        # Kalshi sends a *signed change* in resting contracts at the
        # price (docs example: "delta_fp": "-54.00"). It is not an
        # absolute count: abs() would turn a 54-contract pull into a
        # 54-contract add, and a 0 delta (no-op) would wipe the level.
        side = str(body.get("side", ""))
        if side not in ("yes", "no"):
            return out
        try:
            price = _to_cents(body.get("price_dollars", body.get("price", 0)))
            delta = _to_contracts(body.get("delta_fp", body.get("count", 0)))
        except (TypeError, ValueError):
            return out
        if delta == 0:
            return out
        levels = (st.orderbook.yes_bids if side == "yes"
                  else st.orderbook.no_bids)
        resting = next((lv.contracts for lv in levels
                        if lv.price_cents == price), 0)
        before = st.orderbook
        st.orderbook = apply_delta(st.orderbook, side, price,
                                   max(0, resting + delta))
        if st.orderbook == before:
            return out  # delta touched nothing resting: stay quiet
        st.updated_ms = _now_ms()
        out.append(Update(kind="orderbook", ticker=ticker, payload={"delta": body}, ts_ms=st.updated_ms))
    elif mtype == "trade":
        out.append(Update(kind="trade", ticker=ticker, payload=body, ts_ms=_now_ms()))
    return out


def subscribe_payload(msg_id: int, channels: list[str], tickers: list[str]) -> str:
    """Contract: exact subscribe wire format Kalshi expects."""
    params: dict[str, Any] = {"channels": channels}
    if tickers:
        params["market_tickers"] = tickers
    return json.dumps({"id": msg_id, "cmd": "subscribe", "params": params})


class KalshiListener:
    """Owns one websocket: subscribes to topics, streams Updates."""

    def __init__(
        self,
        creds: Credentials | None,
        on_update: Callable[[Update], None] | None = None,
        url: str | None = None,
    ) -> None:
        self.creds = creds
        self.on_update = on_update or (lambda u: None)
        self.url = url  # override for tests / local relays
        self.state: dict[str, TickerState] = {}
        self.tickers: list[str] = []
        self._stop = False

    @property
    def live(self) -> bool:
        return self.creds is not None

    def watch(self, tickers: list[str]) -> None:
        self.tickers = sorted(set(tickers))
        # The loop rotates topics; keep only state we can still serve.
        keep = set(self.tickers)
        self.state = {t: s for t, s in self.state.items() if t in keep}

    async def run(self) -> None:
        """Contract: reconnect forever until stop(); REST fallback when keyless."""
        if not self.live:
            await self._poll_loop()
            return
        backoff = 1
        while not self._stop:
            try:
                await self._session()
                backoff = 1
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self) -> None:
        self._stop = True

    async def _session(self) -> None:
        import websockets

        assert self.creds is not None
        url = self.url or WS_URLS.get(self.creds.env, WS_URLS["demo"])
        headers = auth_headers("GET", WS_PATH, self.creds)
        async with websockets.connect(url, additional_headers=headers) as ws:
            await ws.send(subscribe_payload(1, CHANNELS, self.tickers))
            async for raw in ws:
                if self._stop:
                    return
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for update in handle_message(msg, self.state):
                    self.on_update(update)

    async def _poll_loop(self, interval_s: int = 15) -> None:
        """Keyless fallback: REST orderbook snapshots on a timer."""
        from sources.kalshi import KalshiSource

        src = KalshiSource()
        while not self._stop:
            for ticker in list(self.tickers):
                try:
                    book = src.fetch_orderbook(ticker)
                    st = self.state.setdefault(ticker, TickerState(ticker=ticker))
                    st.orderbook = book
                    st.updated_ms = _now_ms()
                    self.on_update(Update(kind="orderbook", ticker=ticker, payload={"levels": "poll"}, ts_ms=st.updated_ms))
                except Exception:
                    continue
            for _ in range(interval_s * 2):
                if self._stop:
                    return
                await asyncio.sleep(0.5)
