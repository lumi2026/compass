"""Live smoke test: GDELT news only. Stdlib only.

Run:  python3 scripts/smoke_gdelt.py [--limit 3] [--query climate]

Public artlist queries need no key. Retries on 429. Exits non-zero on failure.
"""

import argparse
import sys

sys.path.insert(0, ".")

from sources.gdelt import GdeltSource


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--query", type=str, default="climate")
    args = parser.parse_args()

    print(f"[gdelt] GET artlist query={args.query} (limit={args.limit}) ...")
    articles = GdeltSource(query=args.query).fetch_normalized(limit=args.limit)
    print(f"[gdelt] got {len(articles)} articles")
    for a in articles[: args.limit]:
        print(f"  - {a.title[:100]} ({a.url[:60]})")
    if not articles:
        print("[gdelt] FAIL: empty result")
        return 1

    print("SMOKE OK: gdelt returned data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
