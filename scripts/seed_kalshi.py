"""Capture a frozen Kalshi seed for offline runs.

Run:  uv run python scripts/seed_kalshi.py --out data/seed.json
"""

import sys

import click

sys.path.insert(0, ".")

from services.seed import capture, capture_events, save


@click.command()
@click.option("--out", default="data/seed.json")
@click.option("--events", type=int, default=3)
@click.option("--markets", type=int, default=3)
@click.option("--event", multiple=True, default=(),
              help="explicit event ticker (repeatable); when given, "
                   "--events count is ignored and these events are "
                   "captured across categories")
def main(out, events, markets, event) -> None:
    if event:
        payload = capture_events(list(event), markets_per_event=markets)
    else:
        payload = capture(events_limit=events, markets_per_event=markets)
    save(out, payload)
    n = len(payload["tickers"])
    print(f"seeded {n} tickers -> {out}")
    sys.exit(0 if n else 1)


if __name__ == "__main__":
    main()
