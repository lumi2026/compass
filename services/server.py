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
"""

import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sources.kalshi import KalshiSource

from .feed import FeedStore
from .listener import KalshiListener


class Service:
    def __init__(self, listener: KalshiListener, store: FeedStore) -> None:
        self.listener = listener
        self.store = store
        self.src = KalshiSource()
        listener.on_update = self._on_update

    def _on_update(self, update) -> None:
        self.store.merge_state(self.listener.state)
        self.store.publish(update)

    def set_topics(self, events: list[str], market_tickers: list[str]) -> list[str]:
        tickers = list(market_tickers)
        for ev in events:
            try:
                tickers.extend(m.ticker for m in self.src.fetch_markets(ev))
            except Exception:
                continue
        tickers = sorted(set(tickers))
        self.listener.watch(tickers)
        return tickers

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
                                 "tickers": service.listener.tickers})
            elif parsed.path == "/topics":
                self._send(200, {"tickers": service.listener.tickers})
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
                tickers = service.set_topics(
                    body.get("events") or [], body.get("market_tickers") or []
                )
                self._send(200, {"tickers": tickers, "live_ws": service.listener.live})
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
