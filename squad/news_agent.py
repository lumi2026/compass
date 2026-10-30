"""Single-agent prototype: explain a Kalshi topic with GDELT news.

Pipeline: Kalshi event markets -> n-gram term search over a webngrams
snapshot -> fetch original articles -> LLM relevance filter + summary
-> Proposal. NewsAgent chains the squad toolbox per the Agent protocol.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import re

from graph import article_id, subject_id
from sources.kalshi import KalshiSource
from sources.ngrams import (
    articles_from_mentions, backfill_snapshots, ngram_mentions,
)
from squad import Proposal
from squad.models import resolve_model
from squad.tools import fetch_article, llm_relevant, llm_summarize, summarize


def kalshi_markets(event_ticker: str, limit: int = 10) -> list:
    """Adapter: live markets (with prices) for a Kalshi event."""
    return KalshiSource().fetch_markets(event_ticker, limit=limit)


@dataclass
class NewsAgent:
    """Single-agent prototype: news summary for one Kalshi topic."""

    name: str = "news-agent"
    snapshot_gz: str | None = None
    max_articles: int = 3
    # Model spec, resolved per task via squad.models.resolve_model:
    # "auto" (role default + env), None (extractive only), a model-id
    # str, a {"provider", "model"} dict, or a LangChain chat instance.
    # A task may override with task["model"].
    model: Any = "auto"

    def run(self, task: dict, context: dict | None = None) -> list[Proposal]:
        ticker = task["event_ticker"]
        terms = task["terms"]
        model = resolve_model(task.get("model", self.model),
                              role=self.name)
        markets = kalshi_markets(ticker)
        lead = markets[0] if markets else None
        price = f"{lead.last_price}c" if lead and lead.last_price is not None else "?"

        mentions = ngram_mentions(terms, self.snapshot_gz or "", per_term_cap=5)
        total = sum(m["count"] for m in mentions.values())

        # Per-article dates: record date, else the snapshot stamp parsed
        # from the snapshot path ("" when neither is known).
        snap_m = re.search(r"(\d{14})", self.snapshot_gz or "")
        snap_ts = snap_m.group(1) if snap_m else ""
        dates = {a.url: a.published_at
                 for a in articles_from_mentions(mentions, snap_ts)}

        # Prefer lead-paragraph hits (pos <= 20), dedupe urls.
        cand: list[str] = []
        for m in mentions.values():
            for s in sorted(m["samples"], key=lambda r: r.get("pos", 99)):
                if s.get("url") and s["url"] not in cand:
                    cand.append(s["url"])
        cand = cand[: self.max_articles]

        run_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = [
            f"date={run_date}",
            f"markets={len(markets)}",
            f"ngram_hits_15min={total}",
            f"lead={lead.title if lead else '?'} @ {price}",
        ]
        ok = 0
        llm_ok = 0
        dropped = 0
        question = lead.title if lead and lead.title else None
        for url in cand:
            try:
                text = fetch_article(url)
            except Exception:
                continue
            if model is not None:
                try:
                    keep, _reason = llm_relevant(text, terms, model,
                                                 question=question)
                    if not keep:
                        dropped += 1
                        continue
                except Exception:
                    pass  # relevance failure is fail-open
            summ, via = "", "extractive"
            if model is not None:
                try:
                    summ = llm_summarize(text, terms, model)
                    if summ:
                        via, llm_ok = "llm", llm_ok + 1
                except Exception:
                    summ, via = "", "extractive"
            if not summ:
                summ = summarize(text, terms)
            if not summ:
                continue
            ok += 1
            stamp = dates.get(url, "")
            lines.append(f"- [{via}] {summ} [{url}]"
                         + (f" ({stamp})" if stamp else ""))
        if not ok:
            lines.append("(no articles fetchable; n-gram counts only)")
        if model is not None:
            lines.append(f"relevant_filter=dropped {dropped} of {len(cand)}")
        mid = getattr(model, "model_name", None) or "?"
        lines.append(f"engine={'llm:' + mid if llm_ok else 'extractive'}")
        conf = min(0.9, 0.3 + 0.15 * ok + (0.1 if total > 5 else 0.0))
        return [
            Proposal(
                agent=self.name,
                action="news_summary",
                entity_id=f"kalshi:{ticker}",
                detail="\n".join(lines),
                confidence=round(conf, 2),
            )
        ]

    def dig(self, task: dict, hours_back: float = 48,
            every_nth: int = 16, max_urls: int = 20,
            dest_dir: str = "/tmp") -> list[dict]:
        """Dig past news: stride-sampled snapshots, deduped by URL.

        Returns [{url, title, subjects, snapshot, seendate}] ready for
        `graph_proposals`. Bounded cost: 48h at every-16th mark ≈ 13
        files. LLM relevance filtering stays a later per-article pass.
        """
        terms = task["terms"]
        seen, kept = set(), []
        snaps = backfill_snapshots(hours_back, every_nth, dest_dir)
        for ts, snap in snaps:
            try:
                mentions = ngram_mentions(terms, snap,
                                          per_term_cap=max_urls)
            except Exception:
                continue
            for a in articles_from_mentions(mentions, ts):
                if a.url in seen:
                    continue
                seen.add(a.url)
                kept.append({"url": a.url, "title": a.title,
                             "subjects": list(terms), "snapshot": ts,
                             "seendate": a.published_at})
                if len(kept) >= max_urls:
                    return kept
        return kept

    def graph_proposals(self, task: dict,
                        kept: list[dict]) -> list[Proposal]:
        """Lane-ready graph proposals for relevance-kept articles.

        `kept`: [{url, title?, subjects?}]; subjects default to the task
        terms (LLM entity extraction replaces this later). Pure offline:
        the Orchestrator gates these through the Skeptic and commits them
        with `graph.proposals_to_commit`, so a news summary flows straight
        into shared graph structure.
        """
        import json

        ticker = task["event_ticker"]
        terms = task.get("terms", [])
        eid = f"kalshi:{ticker}"
        props: list[Proposal] = []
        for a in kept:
            aid = article_id(a["url"])
            props.append(Proposal(
                agent=self.name, action="add-node", entity_id=aid,
                detail=json.dumps({"kind": "Article",
                                   "title": a.get("title", a["url"]),
                                   "valid_at": a.get("seendate", "")}),
                confidence=0.8))
            for term in a.get("subjects") or terms:
                sid = subject_id(term)
                props.append(Proposal(
                    agent=self.name, action="add-node", entity_id=sid,
                    detail=json.dumps({"kind": "Subject", "title": term}),
                    confidence=0.7))
                props.append(Proposal(
                    agent=self.name, action="add-edge", entity_id=aid,
                    detail=json.dumps({"src": aid, "dst": sid,
                                       "rel": "mentions"}),
                    confidence=0.75))
            props.append(Proposal(
                agent=self.name, action="add-edge", entity_id=aid,
                detail=json.dumps({"src": aid, "dst": eid, "rel": "about"}),
                confidence=0.75))
        return props
