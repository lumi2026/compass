"""Seed capture + load so the service runs offline from frozen data.

Contract: `capture` pulls live data into a plain-JSON payload;
`load` restores it into a FeedStore with no network. Payload shape:
{"tickers": {ticker: {"last": {...}, "yes": [[p, c]...], "no": [...],
 "candles": [{...}]}}}.
"""

import json
import time

from sources.kalshi import Candle, KalshiSource, OrderBookSnapshot, PriceLevel

from .feed import FeedStore
from .listener import TickerState, Update


def capture(
    events_limit: int = 3,
    markets_per_event: int = 3,
    hours: int = 24,
    interval: int = 60,
) -> dict:
    src = KalshiSource()
    now = int(time.time())
    payload: dict = {"tickers": {}}
    for event in src.fetch_normalized(limit=events_limit):
        for market in src.fetch_markets(event.external_id, limit=markets_per_event):
            try:
                book = src.fetch_orderbook(market.ticker)
            except Exception:
                book = OrderBookSnapshot(ticker=market.ticker, yes_bids=[], no_bids=[])
            try:
                candles = src.fetch_candlesticks(
                    [market.ticker], now - hours * 3600, now, interval
                ).get(market.ticker, [])
            except Exception:
                candles = []
            payload["tickers"][market.ticker] = {
                "last": {"title": market.title, "yes_bid": market.yes_bid,
                         "yes_ask": market.yes_ask, "last_price": market.last_price},
                "yes": [[lv.price_cents, lv.contracts] for lv in book.yes_bids],
                "no": [[lv.price_cents, lv.contracts] for lv in book.no_bids],
                "candles": [
                    {"end_ts": c.end_ts, "open": c.open, "high": c.high,
                     "low": c.low, "close": c.close, "volume": c.volume}
                    for c in candles
                ],
            }
    return payload


def capture_events(
    event_tickers: list[str],
    markets_per_event: int = 3,
    hours: int = 24,
    interval: int = 60,
) -> dict:
    """Capture specific events (across categories) into a seed payload.

    Same shape as `capture`; used for the MVP demo seed so offline runs
    cover a fixed cross-category event set instead of whatever tops the
    live events list at capture time.
    """
    src = KalshiSource()
    now = int(time.time())
    payload: dict = {"tickers": {}}
    for event_ticker in event_tickers:
        try:
            markets = src.fetch_markets(event_ticker, limit=markets_per_event)
        except Exception:
            continue
        for market in markets:
            try:
                book = src.fetch_orderbook(market.ticker)
            except Exception:
                book = OrderBookSnapshot(ticker=market.ticker, yes_bids=[], no_bids=[])
            try:
                candles = src.fetch_candlesticks(
                    [market.ticker], now - hours * 3600, now, interval
                ).get(market.ticker, [])
            except Exception:
                candles = []
            payload["tickers"][market.ticker] = {
                "last": {"title": market.title, "yes_bid": market.yes_bid,
                         "yes_ask": market.yes_ask, "last_price": market.last_price},
                "yes": [[lv.price_cents, lv.contracts] for lv in book.yes_bids],
                "no": [[lv.price_cents, lv.contracts] for lv in book.no_bids],
                "candles": [
                    {"end_ts": c.end_ts, "open": c.open, "high": c.high,
                     "low": c.low, "close": c.close, "volume": c.volume}
                    for c in candles
                ],
            }
    return payload


def save(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load(path: str, store: FeedStore) -> list[str]:
    """Contract: restores snapshots + history, returns tickers. No network."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    tickers = sorted(payload.get("tickers", {}))
    for ticker, entry in payload["tickers"].items():
        st = TickerState(
            ticker=ticker,
            last=entry.get("last", {}),
            orderbook=OrderBookSnapshot(
                ticker=ticker,
                yes_bids=[PriceLevel(p, c) for p, c in entry.get("yes", [])],
                no_bids=[PriceLevel(p, c) for p, c in entry.get("no", [])],
            ),
            updated_ms=int(time.time() * 1000),
        )
        store.ingest_single(ticker, st)
        store.publish(Update(kind="ticker", ticker=ticker,
                             payload={"seed": True, **st.last}, ts_ms=st.updated_ms))
    return tickers
