"""Capture a frozen Kalshi seed for offline runs.

Run:  uv run python scripts/seed_kalshi.py --out data/seed.json
"""

import argparse
import sys

sys.path.insert(0, ".")

from services.seed import capture, save


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/seed.json")
    parser.add_argument("--events", type=int, default=3)
    parser.add_argument("--markets", type=int, default=3)
    args = parser.parse_args()

    payload = capture(events_limit=args.events, markets_per_event=args.markets)
    save(args.out, payload)
    n = len(payload["tickers"])
    print(f"seeded {n} tickers -> {args.out}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
