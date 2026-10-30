"""Scout: discover Event + Market nodes for one Kalshi event.

Pure discovery lane (ROLE_ACTIONS["scout"] == ("add-node",) only).
Live markets come from Kalshi unless the caller injects them via
context["markets"] or markets_fn (tests / seeded offline runs).
Market nodes carry live odds in props; each refresh is a logged
batch, so the commit log holds the odds history for time travel.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
import json

from squad import Proposal


def market_odds(m: Any) -> dict:
    """Live odds payload for a market (Kalshi contract or seed stand-in)."""
    def _num(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    odds = {"odds_at": datetime.now(timezone.utc).isoformat()}
    for key in ("last_price", "yes_bid", "yes_ask", "volume_24h"):
        v = getattr(m, key, None)
        if isinstance(m, dict):
            v = m.get(key, v)
        v = _num(v)
        if v is not None:
            odds[{"last_price": "price_cents"}.get(key, key)] = v
    return odds


def _task_ticker(task: dict | str) -> str:
    if isinstance(task, str):
        return task
    return task.get("event_ticker", "")


def _market_ticker(m: Any) -> str:
    t = getattr(m, "ticker", None)
    if t:
        return str(t)
    if isinstance(m, dict):
        return str(m.get("ticker", ""))
    return ""


def _market_title(m: Any) -> str:
    t = getattr(m, "title", None)
    if t:
        return str(t)
    if isinstance(m, dict):
        return str(m.get("title", ""))
    return ""


def _default_markets(event_ticker: str) -> list:
    from sources.kalshi import KalshiSource

    return KalshiSource().fetch_markets(event_ticker)


@dataclass
class Scout:
    """Discovery agent: one Event node + one Market node per contract.

    Alias ScoutAgent exists for the plan naming.
    """

    name: str = "scout"
    markets_fn: Callable[[str], list] | None = None

    def run(self, task: dict | str, context: dict | None = None) -> list[Proposal]:
        ticker = _task_ticker(task)
        if not ticker:
            return []
        ctx = context or {}
        markets: Any = ctx.get("markets")
        if markets is None:
            fn = self.markets_fn or _default_markets
            try:
                markets = fn(ticker)
            except Exception:
                markets = []
        task_title = task.get("event_title", "") if isinstance(task, dict) else ""
        lead_title = _market_title(markets[0]) if markets else ""
        event_title = task_title or lead_title or ticker
        props = [Proposal(
            agent=self.name, action="add-node",
            entity_id=f"kalshi:{ticker}",
            detail=json.dumps({"kind": "Event", "title": event_title}),
            confidence=0.85,
        )]
        for m in markets or []:
            mt = _market_ticker(m)
            if not mt:
                continue
            props.append(Proposal(
                agent=self.name, action="add-node",
                entity_id=f"market:{mt}",
                detail=json.dumps(
                    {"kind": "Market",
                     "title": f"{_market_title(m) or mt} [{mt}]",
                     "props": market_odds(m)}),
                confidence=0.8,
            ))
        return props


ScoutAgent = Scout
