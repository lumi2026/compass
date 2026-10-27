"""Live smoke test: Kalshi events only. Stdlib only.

Run:  python3 scripts/smoke_kalshi.py [--limit 3]

Public reads need no API key. Exits non-zero on failure.
"""

import argparse
import sys

sys.path.insert(0, ".")

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

    print("SMOKE OK: kalshi returned data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
