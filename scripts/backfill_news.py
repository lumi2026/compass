"""Dig past news for one Kalshi topic: sampled snapshots -> digest.

Run:  python3 scripts/backfill_news.py --event-ticker KXSPACEXMARS-30
      --terms spacex,starlink --hours-back 48 --out /tmp/backfill.json

Offline-safe except snapshot downloads. Writes a digest (same shape as
collect_news) plus --graph-out JSON of lane-ready graph proposals.
"""

import argparse
import datetime
import json
import sys
import tempfile
import os

sys.path.insert(0, ".")

from scripts.collect_news import summarize_topic  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event-ticker", required=True)
    p.add_argument("--terms", required=True,
                   help="comma-separated watch phrases")
    p.add_argument("--hours-back", type=float, default=48)
    p.add_argument("--every-nth", type=int, default=16)
    p.add_argument("--max-urls", type=int, default=20)
    p.add_argument("--out", default="/tmp/backfill_digest.json")
    p.add_argument("--graph-out", default="/tmp/backfill_graph.json")
    args = p.parse_args()

    task = {"event_ticker": args.event_ticker,
            "terms": [w.strip() for w in args.terms.split(",") if w.strip()]}
    ag = NewsAgent(model=None)
    kept = ag.dig(task, hours_back=args.hours_back,
                  every_nth=args.every_nth, max_urls=args.max_urls)
    print(f"[backfill] {len(kept)} urls across snapshots")

    now = datetime.datetime.now(datetime.timezone.utc)
    from sources.base import NewsArticle  # noqa: E402

    arts = [NewsArticle(source="ngrams", external_id=k["url"],
                        title=k["title"], url=k["url"],
                        published_at=k["seendate"],
                        raw={"snapshot": k["snapshot"]}) for k in kept]
    digest = {"collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "source": "ngrams-backfill",
              "topics": {args.event_ticker: summarize_topic(
                  args.event_ticker, args.terms, arts, now=now)},
              "errors": {}}
    with tempfile.NamedTemporaryFile(
            "w", dir=os.path.dirname(args.out) or ".",
            delete=False) as f:
        json.dump(digest, f, indent=1)
        tmp = f.name
    os.replace(tmp, args.out)
    print(f"[backfill] wrote {args.out}")

    props = ag.graph_proposals(task, kept)
    with open(args.graph_out, "w") as f:
        json.dump([{"agent": x.agent, "action": x.action,
                    "entity_id": x.entity_id, "detail": x.detail,
                    "confidence": x.confidence} for x in props], f, indent=1)
    print(f"[backfill] wrote {len(props)} proposals to {args.graph_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
