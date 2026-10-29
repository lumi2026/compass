"""MVP: seed Kalshi events across categories -> news scan -> graph -> Cytoscape page.

One shared GraphStore across all tasks so cross-event links (Connector
derive) actually appear. Writes graph JSON + a standalone HTML viewer.

Run:
  uv run python scripts/run_mvp.py --seed data/seed_mvp.json
  uv run python scripts/run_mvp.py --snapshot /tmp/x.json.gz --seed data/seed_mvp.json
  # writes demo/mvp_graph.json + demo/mvp_graph.html;
  # then open the HTML file in a browser

Offline: pass --snapshot fixture and/or --seed JSON.
Live: defaults walk back --lookback snapshots per event.
"""

import argparse
import json
import sys

sys.path.insert(0, ".")

from graph import GraphStore, GraphUpdateAPI, proposals_to_commit  # noqa: E402
from squad import Orchestrator, Skeptic  # noqa: E402
from squad.connector import Connector  # noqa: E402
from squad.investigator import Investigator  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402
from squad.pipeline import build_investigator_context  # noqa: E402
from squad.scout import Scout  # noqa: E402
from workspace import to_cytoscape  # noqa: E402
from workspace.viewer import render_page  # noqa: E402

# Cross-category MVP set: Fed/politics, space, AI.
TASKS = [
    {"event_ticker": "KXWARSHOUT-26JUL",
     "terms": ["kevin warsh", "federal reserve", "rate cut"]},
    {"event_ticker": "KXSPACEXMARS-30",
     "terms": ["spacex", "starlink", "elon musk"]},
    {"event_ticker": "KXTOPLLMGENERAL-30",
     "terms": ["artificial intelligence", "openai", "anthropic"]},
]


def event_of(market_ticker: str) -> str:
    head, _, tail = market_ticker.rpartition("-")
    if tail.isalpha() and len(tail) >= 2:
        return head
    return market_ticker


class SeedMarket:
    def __init__(self, ticker, title):
        self.ticker = ticker
        self.title = title


def seed_markets(seed_path: str, event: str) -> list:
    try:
        tickers = json.load(open(seed_path))["tickers"]
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for mt, m in tickers.items():
        if mt == event or mt.startswith(event) or event_of(mt) == event:
            out.append(SeedMarket(mt, m.get("last", {}).get("title", mt)))
    return out


def scan_kept(task: dict, snapshot: str | None, lookback: int,
              max_articles: int) -> tuple[list[dict], list[str]]:
    """Return (kept articles, used snapshot ids) for one task."""
    from sources.ngrams import articles_from_mentions  # noqa: E402

    agent = NewsAgent(snapshot_gz=snapshot, max_articles=max_articles)
    import os as _os

    if snapshot and _os.path.exists(snapshot):
        from sources.ngrams import ngram_mentions  # noqa: E402

        mentions = ngram_mentions(task["terms"], snapshot, per_term_cap=20)
        kept = [{"url": a.url, "title": a.title, "subjects": list(task["terms"]),
                 "snapshot": snapshot, "seendate": a.published_at}
                for a in articles_from_mentions(mentions, snapshot)]
        return kept[: max_articles * 3], [snapshot]
    from sources.ngrams import mentions_with_lookback  # noqa: E402

    mentions, used = mentions_with_lookback(
        task["terms"], max_snapshots=lookback, per_term_cap=20)
    kept = [{"url": a.url, "title": a.title, "subjects": list(task["terms"]),
             "snapshot": used[0][0], "seendate": a.published_at}
            for a in articles_from_mentions(mentions, used[0][0])] if used else []
    return kept[: max_articles * 3], [ts for ts, _ in used]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", default=None)
    p.add_argument("--lookback", type=int, default=8)
    p.add_argument("--seed", default="data/seed.json")
    p.add_argument("--out", default="demo/mvp_graph.json")
    p.add_argument("--html", default="demo/mvp_graph.html")
    p.add_argument("--max-articles", type=int, default=3)
    p.add_argument("--langsmith-project", default=None)
    args = p.parse_args()

    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    ls = setup_langsmith(project=args.langsmith_project)
    print(f"[mvp] langsmith={'on' if ls['enabled'] else 'off'} "
          f"project={ls['project']} events={len(TASKS)}")

    with squad_span("mvp-run", {"events": [t["event_ticker"] for t in TASKS]}) as span:
        api = GraphUpdateAPI(GraphStore())
        skeptic = Skeptic()
        total_kept = 0
        for task in TASKS:
            print(f"[mvp] === {task['event_ticker']} {task['terms']} ===")
            # Scout: live markets, else seed fallback.
            markets = None
            try:
                from sources.kalshi import KalshiSource  # noqa: E402

                markets = KalshiSource().fetch_markets(task["event_ticker"])
            except Exception:
                markets = []
            if not markets:
                markets = seed_markets(args.seed, task["event_ticker"])
                print(f"[mvp] seed fallback: {len(markets)} markets")
            orch = Orchestrator(agents=[Scout()], skeptic=skeptic)
            for prop in orch.investigate(task, {"markets": markets}):
                print(proposals_to_commit(api, prop.agent, prop.action,
                                          prop.entity_id, prop.detail).reason)
            # News scan -> graph proposals.
            try:
                kept, used = scan_kept(task, args.snapshot, args.lookback,
                                       args.max_articles)
            except RuntimeError:
                print("[mvp] FAIL: no snapshot (pass --snapshot fixture offline)")
                return 1
            print(f"[mvp] snapshots: {used} kept={len(kept)}")
            total_kept += len(kept)
            agent = NewsAgent()
            for prop in skeptic.review(
                    agent.graph_proposals(task, kept))[0]:
                print(proposals_to_commit(api, prop.agent, prop.action,
                                          prop.entity_id, prop.detail).reason)
            # Investigator hypothesis.
            ictx = build_investigator_context(kept=kept, markets=markets)
            for prop in skeptic.review(Investigator().run(task, ictx))[0]:
                print(proposals_to_commit(api, prop.agent, prop.action,
                                          prop.entity_id, prop.detail).reason)
        # Connector derive once over the shared store.
        (intent,) = Connector().run(TASKS[0])
        print(proposals_to_commit(api, intent.agent, intent.action,
                                  intent.entity_id, intent.detail).reason)
        for t in TASKS:
            print(f"[mvp] followups {t['event_ticker']}:",
                  Connector().followups(api.store, t["event_ticker"]))

        payload = to_cytoscape(list(api.store.nodes.values()), api.store.edges)
        api.save(args.out)
        with open(args.html, "w") as f:
            f.write(render_page(payload, title="Compass MVP graph"))
        print(f"[mvp] nodes={len(api.store.nodes)} edges={len(api.store.edges)} "
              f"kept={total_kept} elements={len(payload['elements'])}")
        print(f"[mvp] graph -> {args.out}")
        print(f"[mvp] view  -> {args.html} (open in a browser)")
        print("[mvp] OK")
        span.outputs = {"nodes": len(api.store.nodes),
                        "edges": len(api.store.edges),
                        "kept": total_kept, "out": args.out, "html": args.html}
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
