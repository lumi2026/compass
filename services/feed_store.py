"""Agent-facing feed: latest state, short history, push delivery.

Contract: listeners publish Updates here; agents read snapshots and
subscribe for pushes. Push has two routes: SSE (`iter_updates`) for
connected clients and webhooks for registered agent URLs.
"""

import queue
import threading
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .kalshi_listener import TickerState, Update

HISTORY_LEN = 200


@dataclass
class FeedStore:
    """Thread-safe hub between the listener and subscribed agents."""

    state: dict[str, TickerState] = field(default_factory=dict)
    history: dict[str, deque] = field(default_factory=dict)
    webhooks: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _bus: queue.Queue = field(default_factory=queue.Queue)

    def publish(self, update: Update) -> None:
        with self._lock:
            hist = self.history.setdefault(update.ticker, deque(maxlen=HISTORY_LEN))
            hist.append(update)
            targets = sorted(self.webhooks)
        self._bus.put(update)
        for url in targets:
            self._post_webhook(url, update)

    def ingest_state(self, state: dict[str, TickerState]) -> None:
        with self._lock:
            self.state = state

    def ingest_single(self, ticker: str, st: TickerState) -> None:
        with self._lock:
            self.state[ticker] = st

    def merge_state(self, fresh: dict[str, TickerState]) -> None:
        """Contract: live updates merge in; seeded detail is never blanked."""
        with self._lock:
            for ticker, new in fresh.items():
                old = self.state.get(ticker)
                if old is None:
                    self.state[ticker] = new
                    continue
                merged_last = dict(old.last)
                merged_last.update({k: v for k, v in new.last.items() if v})
                self.state[ticker] = TickerState(
                    ticker=ticker,
                    last=merged_last,
                    orderbook=new.orderbook or old.orderbook,
                    updated_ms=max(new.updated_ms, old.updated_ms),
                )

    def snapshot(self, tickers: list[str] | None = None) -> dict[str, dict]:
        with self._lock:
            keys = tickers if tickers else sorted(self.state)
            out: dict[str, dict] = {}
            for t in keys:
                st = self.state.get(t)
                if st is None:
                    continue
                out[t] = {
                    "last": st.last,
                    "top_yes": _top(st, "yes"),
                    "top_no": _top(st, "no"),
                    "updated_ms": st.updated_ms,
                }
            return out

    def recent(self, ticker: str, limit: int = 20) -> list[dict]:
        with self._lock:
            hist = list(self.history.get(ticker, []))[-limit:]
        return [{"kind": u.kind, "payload": u.payload, "ts_ms": u.ts_ms} for u in hist]

    def subscribe_webhook(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        with self._lock:
            self.webhooks.add(url)
        return True

    def unsubscribe_webhook(self, url: str) -> bool:
        with self._lock:
            if url in self.webhooks:
                self.webhooks.remove(url)
                return True
            return False

    def iter_updates(self, timeout_s: float = 20.0):
        """Contract: SSE route. Yields Updates, None on timeout (keepalive)."""
        try:
            yield self._bus.get(timeout=timeout_s)
        except queue.Empty:
            yield None

    def _post_webhook(self, url: str, update: Update) -> None:
        import json

        body = json.dumps(
            {"kind": update.kind, "ticker": update.ticker,
             "payload": update.payload, "ts_ms": update.ts_ms}
        ).encode()
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception:
            with self._lock:
                self.webhooks.discard(url)


def _top(st: TickerState, side: str) -> dict[str, Any] | None:
    book = st.orderbook
    if book is None:
        return None
    levels = book.yes_bids if side == "yes" else book.no_bids
    if not levels:
        return None
    return {"price_cents": levels[0].price_cents, "contracts": levels[0].contracts}
