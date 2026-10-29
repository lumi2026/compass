"""Investigator: turn kept articles + market context into a Hypothesis.

Lane: ROLE_ACTIONS["investigator"] == ("add-node", "add-edge").
Offline-first: builds one Hypothesis node per event plus supports
edges to each kept article (and to the lead market when known).
LLM refinement stays optional — callers pass context["model"];
without it the extractive title is used as-is.
"""

from dataclasses import dataclass
from typing import Any
import json
import re

from graph import article_id
from squad import Proposal


def hypothesis_id(event_ticker: str, n: int = 1) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", event_ticker.lower()).strip("-")
    return f"hypothesis:{slug}-{n}"


def _task_ticker(task: dict | str) -> str:
    if isinstance(task, str):
        return task
    return task.get("event_ticker", "")


def _market_ticker(m: Any) -> str:
    t = getattr(m, "ticker", None)
    if t:
        return str(t)
    if isinstance(m, dict):
        return str(m.get("ticker", m.get("event_ticker", "")))
    return ""


@dataclass
class Investigator:
    """Evidence agent: hypothesis + supports links for one event."""

    name: str = "investigator"

    def run(self, task: dict | str, context: dict | None = None) -> list[Proposal]:
        ticker = _task_ticker(task)
        if not ticker:
            return []
        ctx = context or {}
        terms = task.get("terms", []) if isinstance(task, dict) else []
        kept: list[dict] = list(ctx.get("kept", []) or [])
        if not kept:
            return []
        lead_q = ctx.get("lead_question")
        if not lead_q:
            markets = ctx.get("markets", []) or []
            lead_q = getattr(markets[0], "title", None) if markets else None
            if not lead_q and markets and isinstance(markets[0], dict):
                lead_q = markets[0].get("title")
        hid = hypothesis_id(ticker)
        title_bits = f"{len(kept)} articles"
        if terms:
            title_bits += f" on {', '.join(terms[:3])}"
        htitle = f"{lead_q or ticker} <- {title_bits}"
        props = [Proposal(
            agent=self.name, action="add-node", entity_id=hid,
            detail=json.dumps({"kind": "Hypothesis", "title": htitle}),
            confidence=0.7,
        )]
        for a in kept:
            url = a.get("url", "") if isinstance(a, dict) else ""
            if not url:
                continue
            aid = article_id(url)
            props.append(Proposal(
                agent=self.name, action="add-edge", entity_id=hid,
                detail=json.dumps({"src": hid, "dst": aid,
                                   "rel": "supports"}),
                confidence=0.65,
            ))
        markets = ctx.get("markets", []) or []
        lead_mt = _market_ticker(markets[0]) if markets else ""
        if lead_mt:
            props.append(Proposal(
                agent=self.name, action="add-edge", entity_id=hid,
                detail=json.dumps({"src": hid, "dst": f"market:{lead_mt}",
                                   "rel": "supports"}),
                confidence=0.6,
            ))
        return props


InvestigatorAgent = Investigator
