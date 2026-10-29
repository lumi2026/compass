"""Single-agent prototype: explain a Kalshi topic with GDELT news.

Pipeline: Kalshi event markets -> n-gram term search over a webngrams
snapshot -> fetch original articles -> LLM relevance filter + summary
-> Proposal. NewsAgent chains the squad toolbox per the Agent protocol.
"""

from dataclasses import dataclass
from typing import Any

from sources.kalshi import KalshiSource
from sources.ngrams import ngram_mentions
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

        # Prefer lead-paragraph hits (pos <= 20), dedupe urls.
        cand: list[str] = []
        for m in mentions.values():
            for s in sorted(m["samples"], key=lambda r: r.get("pos", 99)):
                if s.get("url") and s["url"] not in cand:
                    cand.append(s["url"])
        cand = cand[: self.max_articles]

        lines = [
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
            lines.append(f"- [{via}] {summ} [{url}]")
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
