"""Capture a frozen Kalshi seed for offline runs.

Run:  uv run python scripts/seed_kalshi.py --out data_local/seed.json
Wide: uv run python scripts/seed_kalshi.py --cross-section \\
          --per-category 2 --out data_local/seed_wide.json
"""

import sys

import click

sys.path.insert(0, ".")

from services.feed_seed import (capture, capture_cross_section,
                                capture_events, parse_cross_section, save)


@click.command()
@click.option("--out", default="data_local/seed.json")
@click.option("--events", type=int, default=3)
@click.option("--markets", type=int, default=3)
@click.option("--event", multiple=True, default=(),
              help="explicit event ticker (repeatable); when given, "
                   "--events count is ignored and these events are "
                   "captured across categories")
@click.option("--cross-section", is_flag=True, default=False,
              help="capture up to --per-category events per bucket "
                   "(politics, economics, science, culture, sports)")
@click.option("--per-category", type=int, default=2,
              help="events per bucket with --cross-section")
@click.option("--buckets", default="",
              help="bucket override, e.g. "
                   "'politics:Politics,Elections sports:Sports'")
def main(out, events, markets, event, cross_section, per_category,
         buckets) -> None:
    if event:
        payload = capture_events(list(event), markets_per_event=markets)
    elif cross_section:
        payload = capture_cross_section(
            parse_cross_section(buckets) or None,
            per_category=per_category, markets_per_event=markets)
    else:
        payload = capture(events_limit=events, markets_per_event=markets)
    save(out, payload)
    n = len(payload["tickers"])
    print(f"seeded {n} tickers -> {out}")
    sys.exit(0 if n else 1)


if __name__ == "__main__":
    main()
