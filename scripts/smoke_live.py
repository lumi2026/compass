"""Live smoke test: Kalshi events + GDELT artlist. Stdlib only.

Run:  source .venv/bin/activate  (or uv run)
      python scripts/smoke_live.py [--limit 3]

Exits non-zero on any failure so the demo never silently runs on empty data.
"""

import argparse
import sys

sys.path.insert(0, ".")

from sources.gdelt import GdeltSource
from sources.kalshi import KalshiSource


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    print(f"[kalshi] GET events (limit={args.limit}) ...")
    events = KalshiSource().fetch_normalized(limit=args.limit)
    print(f"[kalshi] got {len(events)} events")
    for e in events[: args.limit]:
        print(f"  - {e.external_id}: {e.title[:100]}")
    if not events:
        print("[kalshi] FAIL: empty result")
        return 1

    print(f"[gdelt] GET artlist query=climate (limit={args.limit}) ...")
    articles = GdeltSource(query="climate").fetch_normalized(limit=args.limit)
    print(f"[gdelt] got {len(articles)} articles")
    for a in articles[: args.limit]:
        print(f"  - {a.title[:100]} ({a.url[:60]})")
    if not articles:
        print("[gdelt] FAIL: empty result")
        return 1

    print("SMOKE OK: both live sources returned data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
