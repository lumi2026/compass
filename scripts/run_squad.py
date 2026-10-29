"""Run the full squad split on one Kalshi event (offline-capable).

Order: Scout -> NewsAgent dig/graph_proposals -> Investigator
(context-wired) -> Connector derive. All writes go through Skeptic +
proposals_to_commit. Only graph_proposals() commits; news_summary
stays log-only.

Run:
  python3 scripts/run_squad.py --event KXSPACEXMARS-30 \\
      --terms spacex starlink "elon musk" [--snapshot /tmp/x.json.gz]
  offline: pass --snapshot fixture and/or --seed data/seed.json
"""

import argparse
import json
import sys

sys.path.insert(0, ".")

from graph import GraphStore, GraphUpdateAPI, proposals_to_commit  # noqa: E402
from sources.ngrams import mentions_with_lookback  # noqa: E402
from squad import Orchestrator, Skeptic  # noqa: E402
from squad.connector import Connector  # noqa: E402
from squad.investigator import Investigator  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402
from squad.pipeline import build_investigator_context  # noqa: E402
from squad.scout import Scout  # noqa: E402
from workspace import to_cytoscape  # noqa: E402


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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event", default="KXSPACEXMARS-30")
    p.add_argument("--terms", nargs="*", default=["spacex", "starlink", "elon musk"])
    p.add_argument("--snapshot", default=None)
    p.add_argument("--lookback", type=int, default=8)
    p.add_argument("--seed", default="data/seed.json")
    p.add_argument("--out", default="/tmp/squad_graph.json")
    p.add_argument("--max-articles", type=int, default=3)
    p.add_argument("--langsmith-project", default=None)
    args = p.parse_args()

    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    ls = setup_langsmith(project=args.langsmith_project)
    print(f"[squad] langsmith={'on' if ls['enabled'] else 'off'} "
          f"project={ls['project']}")

    with squad_span("squad-run", {"event": args.event,
                                  "terms": args.terms}) as span:
        result = _run(args)
        span.outputs = result
    return 0


def _run(args) -> dict:
    task = {"event_ticker": args.event, "terms": args.terms}
    snap = args.snapshot  # explicit fixture only; otherwise lookback below

    scout = Scout()
    markets = scout.markets_fn(args.event) if scout.markets_fn else None
    if markets is None:
        try:
            from sources.kalshi import KalshiSource

            markets = KalshiSource().fetch_markets(args.event)
        except Exception:
            markets = []
    if not markets:
        markets = seed_markets(args.seed, args.event)
        print(f"[squad] live markets empty, seed fallback: {len(markets)}")
    ctx = {"markets": markets}
    orch = Orchestrator(agents=[scout], skeptic=Skeptic())
    api = GraphUpdateAPI(GraphStore())
    for prop in orch.investigate(task, ctx):
        print(proposals_to_commit(api, prop.agent, prop.action,
                                  prop.entity_id, prop.detail).reason)

    agent = NewsAgent(snapshot_gz=snap, max_articles=args.max_articles)
    import os as _os

    if snap and _os.path.exists(snap):
        from sources.ngrams import articles_from_mentions, ngram_mentions

        mentions = ngram_mentions(task["terms"], snap, per_term_cap=20)
        kept = [{"url": a.url, "title": a.title, "subjects": list(task["terms"]),
                 "snapshot": snap, "seendate": a.published_at}
                for a in articles_from_mentions(mentions, snap)][: args.max_articles * 3]
        print(f"[squad] snapshot kept {len(kept)} urls")
    else:
        from sources.ngrams import articles_from_mentions  # noqa: E402

        try:
            mentions, used = mentions_with_lookback(
                task["terms"], max_snapshots=args.lookback, per_term_cap=20)
        except RuntimeError:
            print("[squad] FAIL: no snapshot (pass --snapshot fixture offline)")
            return 1
        print(f"[squad] snapshots scanned: {[ts for ts, _ in used]}")
        kept = [{"url": a.url, "title": a.title, "subjects": list(task["terms"]),
                 "snapshot": used[0][0], "seendate": a.published_at}
                for a in articles_from_mentions(mentions, used[0][0])][: args.max_articles * 3]
        print(f"[squad] lookback kept {len(kept)} urls")
    for prop in Skeptic().review(agent.graph_proposals(task, kept))[0]:
        print(proposals_to_commit(api, prop.agent, prop.action,
                                  prop.entity_id, prop.detail).reason)

    ictx = build_investigator_context(kept=kept, markets=markets)
    for prop in Skeptic().review(Investigator().run(task, ictx))[0]:
        print(proposals_to_commit(api, prop.agent, prop.action,
                                  prop.entity_id, prop.detail).reason)

    con = Connector()
    (intent,) = con.run(task)
    print(proposals_to_commit(api, intent.agent, intent.action,
                              intent.entity_id, intent.detail).reason)
    print("followups:", con.followups(api.store, args.event))

    payload = to_cytoscape(list(api.store.nodes.values()), api.store.edges)
    api.save(args.out)
    print(f"nodes={len(api.store.nodes)} edges={len(api.store.edges)} "
          f"elements={len(payload['elements'])} -> {args.out}")
    print("[squad] OK")
    return {"nodes": len(api.store.nodes), "edges": len(api.store.edges),
            "out": args.out}


if __name__ == "__main__":
    raise SystemExit(main())
