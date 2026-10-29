"""Pre-LLM smoke test: Kalshi event -> n-gram search -> URLs -> raw text.

Validates every stage before the LLM: markets live, snapshot resolves,
terms hit, articles fetchable and text worth summarizing.

Run:  python3 scripts/smoke_news_pipeline.py [--event TICKER]
      [--term WORD ...] [--max-articles N]

Article texts are dumped to /tmp/smoke_articles/ for inspection.
Stdlib + squad tools only.
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, ".")

from sources.ngrams import (  # noqa: E402
    download_snapshot,
    ngram_mentions,
    snapshot_candidates,
)
from squad.news_agent import kalshi_markets  # noqa: E402
from squad.tools import fetch_article  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event", default="KXSPACEXMARS-30")
    p.add_argument("--term", action="append", default=None)
    p.add_argument("--max-articles", type=int, default=3)
    p.add_argument("--outdir", default="/tmp/smoke_articles")
    args = p.parse_args()
    args.term = args.term or ["spacex"]

    print(f"[smoke] kalshi {args.event} ...")
    markets = kalshi_markets(args.event)
    if not markets:
        print("[smoke] FAIL: no markets")
        return 1
    lead = markets[0]
    print(f"[smoke] markets={len(markets)} lead={lead.title[:80]} @ "
          f"{lead.last_price}c")

    print("[smoke] resolving snapshot ...")
    now = time.time()
    ts = None
    for cand in snapshot_candidates(now):
        dest = f"/tmp/ngram_{cand}.json.gz"
        if not os.path.exists(dest):
            try:
                download_snapshot(cand, dest)
            except Exception as e:
                print(f"[smoke]   {cand} miss ({e})")
                continue
        ts, snap = cand, dest
        break
    if ts is None:
        print("[smoke] FAIL: no snapshot available")
        return 1
    print(f"[smoke] snapshot {ts} ({os.path.getsize(snap)//1_000_000} MB)")

    mentions = ngram_mentions(args.term, snap)
    total = sum(m["count"] for m in mentions.values())
    print(f"[smoke] terms={args.term} hits_15min={total}")
    for t, m in mentions.items():
        print(f"[smoke]   {t}: count={m['count']} urls={len(m['urls'])}")
    urls = []
    for m in mentions.values():
        for s in sorted(m["samples"], key=lambda r: r.get("pos", 99)):
            if s.get("url") and s["url"] not in urls:
                urls.append(s["url"])
    urls = urls[: args.max_articles]
    if not urls:
        print("[smoke] FAIL: no article URLs")
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    ok = 0
    for i, url in enumerate(urls):
        print(f"[smoke] fetching [{i}] {url[:100]} ...")
        try:
            text = fetch_article(url)
        except Exception as e:
            print(f"[smoke]   FAIL {e}")
            continue
        fn = os.path.join(args.outdir, f"article_{i}.txt")
        with open(fn, "w") as f:
            f.write(url + "\n\n" + text)
        words = len(text.split())
        print(f"[smoke]   ok {words} words -> {fn}")
        print(f"[smoke]   excerpt: {text[:200].strip()!r}")
        ok += 1
    print(f"[smoke] {ok}/{len(urls)} articles dumped to {args.outdir}")
    print("SMOKE OK" if ok else "SMOKE FAIL: no text")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
