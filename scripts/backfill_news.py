"""Dig past news for one Kalshi topic: sampled snapshots -> digest.

Run:  python3 scripts/backfill_news.py --event-ticker KXSPACEXMARS-30
      --terms spacex,starlink --hours-back 48 --out /tmp/backfill.json

Offline-safe except snapshot downloads. Writes a digest (same shape as
collect_news) plus --graph-out JSON of lane-ready graph proposals.
"""

import datetime
import json
import sys
import tempfile
import os

import click

sys.path.insert(0, ".")

from scripts.collect_news import summarize_topic  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402


@click.command()
@click.option("--event-ticker", required=True)
@click.option("--terms", required=True,
              help="comma-separated watch phrases")
@click.option("--hours-back", type=float, default=48)
@click.option("--every-nth", type=int, default=16)
@click.option("--max-urls", type=int, default=20)
@click.option("--out", default="/tmp/backfill_digest.json")
@click.option("--graph-out", default="/tmp/backfill_graph.json")
def main(event_ticker, terms, hours_back, every_nth, max_urls, out,
         graph_out) -> None:
    task = {"event_ticker": event_ticker,
            "terms": [w.strip() for w in terms.split(",") if w.strip()]}
    ag = NewsAgent(model=None)
    kept = ag.dig(task, hours_back=hours_back,
                  every_nth=every_nth, max_urls=max_urls)
    print(f"[backfill] {len(kept)} urls across snapshots")

    now = datetime.datetime.now(datetime.timezone.utc)
    from sources.base import NewsArticle  # noqa: E402

    arts = [NewsArticle(source="ngrams", external_id=k["url"],
                        title=k["title"], url=k["url"],
                        published_at=k["seendate"],
                        raw={"snapshot": k["snapshot"]}) for k in kept]
    digest = {"collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "source": "ngrams-backfill",
              "topics": {event_ticker: summarize_topic(
                  event_ticker, terms, arts, now=now)},
              "errors": {}}
    with tempfile.NamedTemporaryFile(
            "w", dir=os.path.dirname(out) or ".",
            delete=False) as f:
        json.dump(digest, f, indent=1)
        tmp = f.name
    os.replace(tmp, out)
    print(f"[backfill] wrote {out}")

    props = ag.graph_proposals(task, kept)
    with open(graph_out, "w") as f:
        json.dump([{"agent": x.agent, "action": x.action,
                    "entity_id": x.entity_id, "detail": x.detail,
                    "confidence": x.confidence} for x in props], f, indent=1)
    print(f"[backfill] wrote {len(props)} proposals to {graph_out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
