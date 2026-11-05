"""Kalshi service tools (read the service, sync topics, sweep)."""

import json
import urllib.parse
import urllib.request


def _get(base: str, path: str, params: dict | None = None,
         timeout: int = 120) -> dict:
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "compass/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post(base: str, path: str, payload: dict,
          timeout: int = 120) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def kalshi_tools(base_url: str) -> dict:
    """{tool_name: {description, func}} bound to one service."""

    def discover_events(limit: int = 30) -> dict:
        """List open Kalshi events (tickers + titles) for topic choice."""
        try:
            data = _get(base_url, "/events", {"limit": limit})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "events": [
            {"event_ticker": e.get("event_ticker", ""),
             "title": e.get("title", "")}
            for e in data.get("events", []) if e.get("event_ticker")]}

    def event_markets(event_ticker: str, limit: int = 50) -> dict:
        """Open markets inside one event (tickers, titles, prices)."""
        try:
            # The service query param is `event` (see kalshi_server
            # `/markets`); `event_ticker` here silently 400s.
            data = _get(base_url, "/markets",
                        {"event": event_ticker, "limit": limit,
                         "status": "open"})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "markets": [
            {"ticker": m.get("ticker", ""),
             "title": m.get("title", m.get("ticker", "")),
             "last_price": m.get("last_price"),
             "status": m.get("status", "")}
            for m in data.get("markets", [])]}

    def sync_watchlist(event_tickers: list) -> dict:
        """Point the service watchlist at these events (tick stream + odds)."""
        try:
            data = _post(base_url, "/topics", {"events": list(event_tickers)})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "tickers": data.get("tickers", [])}

    def trigger_sweep() -> dict:
        """Publish the full book for watched tickers into the graph API."""
        try:
            data = _post(base_url, "/publish/sweep", {})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        published = bool(data.get("published", False))
        return {"ok": published,
                "published": published,
                "tickers": data.get("tickers", 0),
                "proposals": data.get("proposals", 0),
                "revision": data.get("revision"),
                "reason": data.get("reason", "")}

    def service_status() -> dict:
        """Health + watchlist size + publish wiring (no full ticker list)."""
        try:
            data = _get(base_url, "/health")
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "live": data.get("live_ws", False),
                "watchlist": len(data.get("tickers", [])),
                "graph_wired": bool(data.get("graph_url", ""))}

    return {
        "kalshi_discover_events": {
            "description": "List open Kalshi events (tickers + titles).",
            "func": discover_events},
        "kalshi_event_markets": {
            "description": "Open markets inside one event.",
            "func": event_markets},
        "kalshi_sync_watchlist": {
            "description": "Point the watchlist at event tickers.",
            "func": sync_watchlist},
        "kalshi_trigger_sweep": {
            "description": "Publish the watched book into the graph API.",
            "func": trigger_sweep},
        "kalshi_service_status": {
            "description": "Health, watchlist size, publish wiring.",
            "func": service_status},
    }
