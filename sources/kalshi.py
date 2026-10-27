"""Kalshi source plugin. Public REST reads, no auth needed for market data.

Covers the full market-data surface the graph needs:
events/topics, per-event markets, order books, and price history
(candlesticks). Batch endpoints are preferred so the squad can pull
many tickers in one request.
"""

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .base import MarketEvent, SourcePlugin

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_S = 20


def _get_json(url: str, retries: int = 3) -> dict:
    import urllib.error

    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "compass-hackathon/0.1"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429 or attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise last  # pragma: no cover


@dataclass(frozen=True)
class MarketContract:
    """One tradable contract inside an event.

    Prices are integer cents converted from Kalshi's `*_dollars` strings.
    """

    source: str
    ticker: str
    event_ticker: str
    title: str
    yes_bid: int | None = None
    yes_ask: int | None = None
    last_price: int | None = None
    volume_24h: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceLevel:
    price_cents: int
    contracts: int


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Yes/no bid levels, best first. Kalshi returns bids only (no asks)."""

    ticker: str
    yes_bids: list[PriceLevel]
    no_bids: list[PriceLevel]


@dataclass(frozen=True)
class Candle:
    """One price-history candlestick for a market."""

    ticker: str
    end_ts: int
    open: int | None
    high: int | None
    low: int | None
    close: int | None
    volume: int = 0


def best_bid(levels: list[PriceLevel]) -> PriceLevel | None:
    """Contract helper: top of book, or None when the side is empty."""
    return levels[0] if levels else None


def _to_cents(price: Any) -> int:
    """Contract: accept integer cents or dollar strings like '0.5400'."""
    if isinstance(price, str) and "." in price:
        return int(round(float(price) * 100))
    return int(price)


def _to_contracts(count: Any) -> int:
    if isinstance(count, str):
        return int(round(float(count)))
    return int(count)


def _parse_levels(raw_levels: Any) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for lvl in raw_levels or []:
        if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            price, count = lvl[0], lvl[1]
        elif isinstance(lvl, dict):
            price = lvl.get("price", lvl.get("dollars", lvl.get("yes", 0)))
            count = lvl.get("count", lvl.get("contracts", lvl.get("no", 0)))
        else:
            continue
        try:
            levels.append(
                PriceLevel(price_cents=_to_cents(price), contracts=_to_contracts(count))
            )
        except (TypeError, ValueError):
            continue
    return levels


class KalshiSource:
    """SourcePlugin for Kalshi: events, markets, order books, candles."""

    name = "kalshi"

    def __init__(self, api_base: str = API_BASE) -> None:
        self.api_base = api_base.rstrip("/")

    # -- Layer 1 contract: events/topics -----------------------------------

    def fetch_raw(self, limit: int = 5) -> list[dict]:
        params = urllib.parse.urlencode({"limit": limit, "status": "open"})
        data = _get_json(f"{self.api_base}/events?{params}")
        events = data.get("events", data if isinstance(data, list) else [])
        return events[:limit]

    def normalize(self, raw: dict) -> MarketEvent:
        external_id = str(raw.get("event_ticker") or raw.get("ticker") or raw.get("id", ""))
        title = str(raw.get("title") or raw.get("event_title") or external_id)
        return MarketEvent(source=self.name, external_id=external_id, title=title, raw=raw)

    def fetch_normalized(self, limit: int = 5) -> list[MarketEvent]:
        return [self.normalize(r) for r in self.fetch_raw(limit)]

    # -- Markets inside an event -------------------------------------------

    def fetch_markets(self, event_ticker: str, limit: int = 20) -> list[MarketContract]:
        params = urllib.parse.urlencode(
            {"event_ticker": event_ticker, "limit": limit, "status": "open"}
        )
        data = _get_json(f"{self.api_base}/markets?{params}")
        markets = data.get("markets", [])
        return [self.normalize_market(m) for m in markets[:limit]]

    def _cents_or_none(self, raw: dict, *keys: str) -> int | None:
        for key in keys:
            if raw.get(key) is not None:
                try:
                    return _to_cents(raw[key])
                except (TypeError, ValueError):
                    continue
        return None

    def normalize_market(self, raw: dict) -> MarketContract:
        volume = raw.get("volume_24h_fp", raw.get("volume_fp"))
        return MarketContract(
            source=self.name,
            ticker=str(raw.get("ticker", "")),
            event_ticker=str(raw.get("event_ticker", "")),
            title=str(raw.get("title") or raw.get("ticker", "")),
            yes_bid=self._cents_or_none(raw, "yes_bid_dollars", "yes_bid"),
            yes_ask=self._cents_or_none(raw, "yes_ask_dollars", "yes_ask"),
            last_price=self._cents_or_none(
                raw, "last_price_dollars", "last_price"
            ),
            volume_24h=_to_contracts(volume) if volume is not None else None,
            raw=raw,
        )

    # -- Order books --------------------------------------------------------

    def fetch_orderbook(self, ticker: str, depth: int = 10) -> OrderBookSnapshot:
        params = urllib.parse.urlencode({"depth": depth})
        data = _get_json(f"{self.api_base}/markets/{ticker}/orderbook?{params}")
        return self.normalize_orderbook(ticker, data.get("orderbook", data))

    def fetch_orderbooks(self, tickers: list[str], depth: int = 10) -> list[OrderBookSnapshot]:
        params = urllib.parse.urlencode(
            {"tickers": ",".join(tickers), "depth": depth}
        )
        data = _get_json(f"{self.api_base}/markets/orderbooks?{params}")
        out = []
        for ticker, book in (data.get("orderbooks") or {}).items():
            out.append(self.normalize_orderbook(ticker, book))
        return out

    def normalize_orderbook(self, ticker: str, raw: dict) -> OrderBookSnapshot:
        book = raw.get("orderbook", raw) if isinstance(raw, dict) else {}
        yes = book.get("yes", book.get("yes_dollars", book.get("yes_dollars_fp", [])))
        no = book.get("no", book.get("no_dollars", book.get("no_dollars_fp", [])))
        return OrderBookSnapshot(
            ticker=ticker, yes_bids=_parse_levels(yes), no_bids=_parse_levels(no)
        )

    # -- Price history (candlesticks) ---------------------------------------

    def fetch_candlesticks(
        self,
        tickers: list[str],
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> dict[str, list[Candle]]:
        params = urllib.parse.urlencode(
            {
                "market_tickers": ",".join(tickers),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            }
        )
        data = _get_json(f"{self.api_base}/markets/candlesticks?{params}")
        out: dict[str, list[Candle]] = {}
        for ticker, candles in (data.get("candlesticks") or {}).items():
            out[ticker] = [self.normalize_candle(ticker, c) for c in candles]
        return out

    def normalize_candle(self, ticker: str, raw: dict) -> Candle:
        price = raw.get("price") or {}
        yes_bid = raw.get("yes_bid") or {}
        return Candle(
            ticker=ticker,
            end_ts=int(raw.get("end_period_ts", 0)),
            open=price.get("open", yes_bid.get("open")),
            high=price.get("high", yes_bid.get("high")),
            low=price.get("low", yes_bid.get("low")),
            close=price.get("close", yes_bid.get("close")),
            volume=int(raw.get("volume", raw.get("volume_fp", 0)) or 0),
        )
