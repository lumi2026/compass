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


HYPOTHESIS_TYPES = ("co_mention", "co_location", "shared_context",
                    "co_outcome")

# Detection mode -> relation type (mirrors MODE_HYPOTHESIS_TYPE in
# scripts/news_loop.py). The Investigator never detects pairs itself;
# the loop detects and passes the relation through task/context.
MODE_HYPOTHESIS_TYPE = {
    "co-mention": "co_mention",
    "shared-subject": "shared_context",
    "burst": "co_mention",
    "entity-overlap": "co_mention",
    "co-location": "co_location",
    "transitive": "shared_context",
    "odds-correlation": "co_outcome",
}


def hypothesis_id(event_ticker: str, n: int = 1) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", event_ticker.lower()).strip("-")
    return f"hypothesis:{slug}-{n}"


def resolve_relation(task: dict | str, context: dict | None = None) -> tuple[str, str]:
    """(type, mode) for this Hypothesis from task/context.

    Accepts task["hypothesis_type"] (closed taxonomy), task["relation"],
    or task/context["mode"] (loop detection mode). Unknown or missing
    values fall back to ("co_mention", "co-mention") so legacy
    single-event hypotheses keep committing.
    """
    ctx = context or {}
    raw_type = ""
    if isinstance(task, dict):
        raw_type = str(task.get("hypothesis_type") or task.get("relation") or "")
    if not raw_type:
        raw_type = str(ctx.get("hypothesis_type") or ctx.get("relation") or "")
    if raw_type in HYPOTHESIS_TYPES:
        inv = {v: k for k, v in MODE_HYPOTHESIS_TYPE.items()}
        return raw_type, inv.get(raw_type, "co-mention")
    raw_mode = ""
    if isinstance(task, dict):
        raw_mode = str(task.get("mode") or "")
    if not raw_mode:
        raw_mode = str(ctx.get("mode") or "")
    htype = MODE_HYPOTHESIS_TYPE.get(raw_mode, "co_mention")
    return htype, (raw_mode if raw_mode in MODE_HYPOTHESIS_TYPE else "co-mention")


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
        htype, hmode = resolve_relation(task, ctx)
        title_bits = f"{len(kept)} articles"
        if terms:
            title_bits += f" on {', '.join(terms[:3])}"
        htitle = f"{lead_q or ticker} <- {title_bits}"
        props = [Proposal(
            agent=self.name, action="add-node", entity_id=hid,
            detail=json.dumps({"kind": "Hypothesis", "title": htitle,
                               "props": {"type": htype, "mode": hmode}}),
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
