"""Kalshi source plugin. Public REST reads, no auth needed for markets/events."""

import json
import urllib.parse
import urllib.request

from .base import MarketEvent, SourcePlugin

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_S = 15


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "compass-hackathon/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.load(resp)


class KalshiSource:
    """SourcePlugin for Kalshi prediction-market events."""

    name = "kalshi"

    def __init__(self, api_base: str = API_BASE) -> None:
        self.api_base = api_base.rstrip("/")

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

