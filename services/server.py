"""Standalone Kalshi feed service over stdlib HTTP.

Agents/clients can:
  GET  /health                    liveness
  POST /topics {events|market_tickers}   set the watchlist (resolves to markets)
  GET  /topics                    current watchlist
  GET  /snapshot?tickers=a,b       latest state (push model starts here)
  GET  /history?ticker=&start=&end=&interval=   historical candles
  GET  /stream                     SSE push of live updates
  POST /subscriptions {url}        register webhook push target
  DELETE /subscriptions            {"url": ...} to remove
  GET  /events?limit=N            open Kalshi events (discovery)
  POST /topics {auto, top_k}       watch top-K events by 24h volume
  GET  /publish/graph             current graph-publish config
  POST /publish/graph {graph_url, move_threshold_cents?}  feed the graph API
"""

import datetime
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sources.kalshi import KalshiSource

from .feed import FeedStore
from .listener import KalshiListener, TickerState, Update

DEFAULT_MOVE_THRESHOLD_CENTS = 5


def event_of(market_ticker: str) -> str:
    """Seed heuristic: strip a trailing -LETTERS market suffix to the event."""
    head, _, tail = market_ticker.rpartition("-")
    if tail.isalpha() and len(tail) >= 2:
        return head
    return market_ticker


def as_cents(v) -> int | None:
    """One price value in cents (dollars string or integer)."""
    if v is None:
        return None
    try:
        if isinstance(v, str) and "." in v:
            return int(round(float(v) * 100))
        return int(v)
    except (TypeError, ValueError):
        return None


def price_cents(last: dict) -> int | None:
    """Best-effort price in cents from a ticker `last` dict.

    Live ticker bodies carry `last_price_dollars`; seeds carry integer
    `last_price`; either way fall back to the yes bid. None when the
    book is empty.
    """
    for key in ("last_price_dollars", "last_price",
                "yes_bid_dollars", "yes_bid"):
        v = as_cents(last.get(key))
        if v is not None:
            return v
    return None


def _world_ts(ts_ms: int) -> str:
    return datetime.datetime.fromtimestamp(
        ts_ms / 1000, tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Service:
    def __init__(self, listener: KalshiListener, store: FeedStore) -> None:
        self.listener = listener
        self.store = store
        self.src = KalshiSource()
        listener.on_update = self._on_update
        # graph-publish state (step 3): only this service touches it, the
        # graph API owns the store. Empty URL = publish disabled.
        self.graph_url = ""
        self.move_threshold_cents = DEFAULT_MOVE_THRESHOLD_CENTS
        self.ticker_event: dict[str, str] = {}
        self.event_titles: dict[str, str] = {}
        self.event_meta: dict[str, dict] = {}
        self._last_pub_price: dict[str, int] = {}
        self._pub_lock = threading.Lock()
        self._pub_busy = False

    def _on_update(self, update) -> None:
        self.store.merge_state(self.listener.state)
        self.store.publish(update)
        self.maybe_publish(update)

    def set_topics(self, events: list[str], market_tickers: list[str]) -> list[str]:
        tickers = list(market_tickers)
        for ev in events:
            try:
                markets = self.src.fetch_markets(ev)
            except Exception:
                continue
            for m in markets:
                self.ticker_event[m.ticker] = ev
                tickers.append(m.ticker)
        for t in market_tickers:
            self.ticker_event.setdefault(t, event_of(t))
        tickers = sorted(set(tickers))
        self.listener.watch(tickers)
        return tickers

    def discover_events(self, limit: int = 10) -> list[dict]:
        """Open Kalshi events for the graph API / news loop to choose from."""
        return [{"event_ticker": e.external_id, "title": e.title}
                for e in self.src.fetch_normalized(limit=limit)]

    @staticmethod
    def _event_meta(raw: dict) -> dict:
        """Keep the display-worthy bits of a raw event dict."""
        meta = {}
        for key in ("category", "series_ticker", "sub_title"):
            v = (raw or {}).get(key)
            if v:
                meta[key] = str(v)
        return meta

    def auto_topics(self, top_k: int = 5, scan_limit: int = 10) -> list[str]:
        """Watch the top-K open events by summed 24h market volume.

        One markets fetch per scanned event; failures are skipped so one
        delisted event never blocks the rotation.
        """
        scored: list[tuple[int, dict, list]] = []
        for ev in self.src.fetch_normalized(limit=scan_limit):
            try:
                markets = self.src.fetch_markets(ev.external_id)
            except Exception:
                continue
            vol = sum(m.volume_24h or 0 for m in markets)
            scored.append((vol, ev, markets))
        scored.sort(key=lambda t: -t[0])
        tickers: list[str] = []
        for _, ev, markets in scored[:max(0, top_k)]:
            self.event_titles[ev.external_id] = ev.title
            meta = self._event_meta(ev.raw or {})
            if meta:
                self.event_meta[ev.external_id] = meta
            for m in markets:
                self.ticker_event[m.ticker] = ev.external_id
                tickers.append(m.ticker)
        tickers = sorted(set(tickers))
        self.listener.watch(tickers)
        return tickers

    def set_graph_publish(self, graph_url: str,
                          move_threshold_cents: int = DEFAULT_MOVE_THRESHOLD_CENTS) -> dict:
        """Point the publish path at a graph API. Empty URL disables it."""
        self.graph_url = (graph_url or "").rstrip("/")
        self.move_threshold_cents = move_threshold_cents
        return {"graph_url": self.graph_url,
                "move_threshold_cents": self.move_threshold_cents}

    def sweep(self) -> dict:
        """Push every watched ticker's current live odds to the graph now.

        REST-polled (no socket wait): refreshes each event's markets,
        updates the store, then posts one Scout + odds batch. First
        sightings set baselines, never moves — same rule as the tick path.
        """
        if not self.graph_url:
            return {"published": False, "reason": "no graph_url"}
        tickers = list(self.listener.tickers)
        if not tickers:
            return {"published": False, "reason": "no tickers"}
        by_event: dict[str, list[str]] = {}
        for t in tickers:
            by_event.setdefault(self.ticker_event.get(t) or event_of(t), []).append(t)
        now_ms = int(time.time() * 1000)
        for ev, ts in by_event.items():
            try:
                markets = self.src.fetch_markets(ev)
            except Exception:
                continue
            for m in markets:
                if m.ticker not in ts:
                    continue
                self.ticker_event[m.ticker] = m.event_ticker
                self.store.ingest_single(
                    m.ticker,
                    TickerState(ticker=m.ticker, updated_ms=now_ms, last={
                        "title": m.title, "yes_bid": m.yes_bid,
                        "yes_ask": m.yes_ask, "last_price": m.last_price}))
        all_props: list[dict] = []
        for t in tickers:
            st = self.store.state.get(t)
            all_props.extend(
                self._proposals_for(t, dict(st.last) if st else {}, now_ms))
        try:
            import urllib.request

            body = json.dumps({"proposals": all_props}).encode()
            req = urllib.request.Request(
                self.graph_url + "/proposals", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                posted = json.loads(resp.read() or b"{}")
            return {"published": True, "tickers": len(tickers),
                    "proposals": len(all_props),
                    "revision": posted.get("revision")}
        except Exception as e:
            print(f"[publish] graph unreachable "
                  f"({e.__class__.__name__}), log-only")
            return {"published": False, "reason": f"graph unreachable: {e}"}

    def _proposals_for(self, ticker: str, last: dict, ts_ms: int) -> list[dict]:
        """Scout discovery nodes (idempotent, odds refreshed) + a move Happening."""
        event = self.ticker_event.get(ticker) or event_of(ticker)
        title = last.get("title") or ticker
        event_title = self.event_titles.get(event, event)
        odds_at = _world_ts(ts_ms)
        market_props: dict = {"odds_at": odds_at, "event_ticker": event}
        now = price_cents(last)
        if now is not None:
            market_props["price_cents"] = now
        for key, prop in (("yes_bid_dollars", "yes_bid"),
                          ("yes_bid", "yes_bid"),
                          ("yes_ask_dollars", "yes_ask"),
                          ("yes_ask", "yes_ask")):
            v = as_cents(last.get(key))
            if v is not None:
                market_props[prop] = v
        event_detail: dict = {"kind": "Event", "title": event_title}
        meta = self.event_meta.get(event)
        if meta:
            event_detail["props"] = dict(meta)
        props = [
            {"agent": "scout", "action": "add-node",
             "entity_id": f"kalshi:{event}",
             "detail": json.dumps(event_detail),
             "confidence": 0.85},
            {"agent": "scout", "action": "add-node",
             "entity_id": f"market:{ticker}",
             "detail": json.dumps({"kind": "Market",
                                   "title": f"{title} [{ticker}]",
                                   "props": market_props}),
             "confidence": 0.8},
        ]
        old = self._last_pub_price.get(ticker)
        if now is not None:
            if old is None:
                self._last_pub_price[ticker] = now  # baseline, not a move
            elif abs(now - old) >= self.move_threshold_cents:
                self._last_pub_price[ticker] = now
                hid = f"happening:market-move-{ticker}-{ts_ms}"
                props.append(
                    {"agent": "news-agent", "action": "add-node",
                     "entity_id": hid,
                     "detail": json.dumps(
                         {"kind": "Happening",
                          "title": f"{ticker} moved {old}c -> {now}c",
                          "valid_at": odds_at,
                          "props": {"market": ticker, "old_cents": old,
                                    "new_cents": now,
                                    "move_cents": now - old}}),
                     "confidence": 0.8})
                props.append(
                    {"agent": "news-agent", "action": "add-edge",
                     "entity_id": hid,
                     "detail": json.dumps(
                         {"src": f"market:{ticker}", "dst": hid,
                          "rel": "relates-to",
                          "valid_at": odds_at}),
                     "confidence": 0.8})
        return props

    def maybe_publish(self, update: Update) -> dict:
        """Best-effort push of one update to the graph API.

        One in-flight POST at most; a busy publisher means the feed is
        ahead, so the update is skipped, not queued. Graph down ->
        log-only, the feed never blocks on it.
        """
        if not self.graph_url:
            return {"published": False, "reason": "no graph_url"}
        with self._pub_lock:
            if self._pub_busy:
                return {"published": False, "reason": "publisher busy"}
            self._pub_busy = True
        try:
            st = self.store.state.get(update.ticker)
            last = dict(st.last) if st else dict(update.payload or {})
            props = self._proposals_for(update.ticker, last, update.ts_ms)
            body = json.dumps({"proposals": props}).encode()
            req = urllib.request.Request(
                self.graph_url + "/proposals", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                posted = json.loads(resp.read() or b"{}")
            return {"published": True, "proposals": len(props),
                    "revision": posted.get("revision")}
        except Exception as e:
            print(f"[publish] graph unreachable "
                  f"({e.__class__.__name__}), log-only")
            return {"published": False, "reason": f"graph unreachable: {e}"}
        finally:
            with self._pub_lock:
                self._pub_busy = False

    def history(self, ticker: str, start: int, end: int, interval: int) -> list[dict]:
        candles = self.src.fetch_candlesticks([ticker], start, end, interval).get(ticker, [])
        return [
            {"ticker": c.ticker, "end_ts": c.end_ts, "open": c.open,
             "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ]


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CompassKalshi/0.1"

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def log_message(self, *args) -> None:  # keep demo output clean
            pass

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/health":
                self._send(200, {"ok": True, "live_ws": service.listener.live,
                                 "tickers": service.listener.tickers,
                                 "graph_url": service.graph_url,
                                 "move_threshold_cents":
                                     service.move_threshold_cents})
            elif parsed.path == "/topics":
                self._send(200, {"tickers": service.listener.tickers})
            elif parsed.path == "/events":
                try:
                    limit = int((qs.get("limit") or ["10"])[0])
                except (ValueError, TypeError):
                    self._send(400, {"error": "limit must be an integer"})
                    return
                try:
                    self._send(200, {"events": service.discover_events(limit)})
                except Exception as e:
                    self._send(502, {"error": f"upstream: {e}"})
            elif parsed.path == "/publish/graph":
                self._send(200, {"graph_url": service.graph_url,
                                 "move_threshold_cents":
                                     service.move_threshold_cents})
            elif parsed.path == "/markets":
                ev = (qs.get("event") or [""])[0]
                try:
                    limit = int((qs.get("limit") or ["20"])[0])
                except (ValueError, TypeError):
                    self._send(400, {"error": "limit must be an integer"})
                    return
                if not ev:
                    self._send(400, {"error": "need ?event=EVENT_TICKER"})
                    return
                try:
                    markets = service.src.fetch_markets(ev, limit=limit)
                except Exception as e:
                    self._send(502, {"error": f"upstream: {e}"})
                    return
                self._send(200, {"markets": [
                    {"ticker": m.ticker, "title": m.title,
                     "last_price": m.last_price, "yes_bid": m.yes_bid,
                     "yes_ask": m.yes_ask, "volume_24h": m.volume_24h}
                    for m in markets]})
            elif parsed.path == "/snapshot":
                tickers = (qs.get("tickers") or [""])[0]
                want = [t for t in tickers.split(",") if t] or None
                self._send(200, service.store.snapshot(want))
            elif parsed.path == "/history":
                try:
                    ticker = qs["ticker"][0]
                    start, end = int(qs["start"][0]), int(qs["end"][0])
                    interval = int((qs.get("interval") or ["60"])[0])
                except (KeyError, IndexError, ValueError):
                    self._send(400, {"error": "need ?ticker=&start=&end=&interval="})
                    return
                try:
                    self._send(200, service.history(ticker, start, end, interval))
                except Exception as e:
                    self._send(502, {"error": f"upstream: {e}"})
            elif parsed.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        update = next(service.store.iter_updates())
                        if update is None:
                            self.wfile.write(b": ping\n\n")
                        else:
                            payload = json.dumps(
                                {"kind": update.kind, "ticker": update.ticker,
                                 "payload": update.payload, "ts_ms": update.ts_ms}
                            )
                            self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._send(404, {"error": "unknown path"})

        def do_POST(self) -> None:
            if self.path == "/topics":
                body = self._read_json()
                if body.get("auto"):
                    try:
                        top_k = int(body.get("top_k", 5))
                        scan = int(body.get("scan_limit", 10))
                    except (ValueError, TypeError):
                        self._send(400, {"error": "top_k/scan_limit must be integers"})
                        return
                    try:
                        tickers = service.auto_topics(top_k=top_k, scan_limit=scan)
                    except Exception as e:
                        self._send(502, {"error": f"upstream: {e}"})
                        return
                    self._send(200, {"tickers": tickers,
                                     "live_ws": service.listener.live,
                                     "mode": "auto", "top_k": top_k})
                else:
                    tickers = service.set_topics(
                        body.get("events") or [], body.get("market_tickers") or []
                    )
                    self._send(200, {"tickers": tickers,
                                     "live_ws": service.listener.live,
                                     "mode": "manual"})
            elif self.path == "/publish/graph":
                body = self._read_json()
                url = str(body.get("graph_url", ""))
                try:
                    threshold = int(body.get("move_threshold_cents",
                                             DEFAULT_MOVE_THRESHOLD_CENTS))
                except (ValueError, TypeError):
                    self._send(400, {"error": "move_threshold_cents must be an integer"})
                    return
                if url and not url.startswith(("http://", "https://")):
                    self._send(400, {"error": "graph_url must be http(s)"})
                    return
                self._send(200, service.set_graph_publish(url, threshold))
            elif self.path == "/publish/sweep":
                try:
                    self._send(200, service.sweep())
                except Exception as e:
                    self._send(502, {"error": f"upstream: {e}"})
            elif self.path == "/subscriptions":
                body = self._read_json()
                ok = service.store.subscribe_webhook(str(body.get("url", "")))
                self._send(200 if ok else 400, {"subscribed": ok})
            else:
                self._send(404, {"error": "unknown path"})

        def do_DELETE(self) -> None:
            if self.path == "/subscriptions":
                body = self._read_json()
                ok = service.store.unsubscribe_webhook(str(body.get("url", "")))
                self._send(200, {"removed": ok})
            else:
                self._send(404, {"error": "unknown path"})

    return Handler


def serve(service: Service, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service))
    return server
