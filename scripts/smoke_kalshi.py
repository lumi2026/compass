"""Live smoke test: Kalshi market data. Stdlib only.

Run:  uv run python scripts/smoke_kalshi.py [--limit 3]

Covers events -> markets -> orderbook -> candles for the first event.
Public reads need no API key. Exits non-zero on failure.
"""

import sys
import time

import click

sys.path.insert(0, ".")

from sources.kalshi import KalshiSource, best_bid


@click.command()
@click.option("--limit", type=int, default=3)
def main(limit) -> None:
    src = KalshiSource()

    print(f"[kalshi] GET events (limit={limit}) ...")
    events = src.fetch_normalized(limit=limit)
    print(f"[kalshi] got {len(events)} events")
    for e in events[: limit]:
        print(f"  - {e.external_id}: {e.title[:100]}")
    if not events:
        print("[kalshi] FAIL: empty events")
        sys.exit(1)

    print(f"[kalshi] GET markets for {events[0].external_id} ...")
    markets = src.fetch_markets(events[0].external_id)
    print(f"[kalshi] got {len(markets)} markets")
    if not markets:
        print("[kalshi] FAIL: empty markets")
        sys.exit(1)
    ticker = markets[0].ticker
    print(f"  - {ticker}: {markets[0].title[:100]}")

    now = int(time.time())
    shown = False
    for m in markets[:3]:
        book = src.fetch_orderbook(m.ticker)
        top_yes, top_no = best_bid(book.yes_bids), best_bid(book.no_bids)
        candles = src.fetch_candlesticks([m.ticker], now - 86400, now, 60).get(m.ticker, [])
        print(f"  - {m.ticker}: yes_levels={len(book.yes_bids)} "
              f"no_levels={len(book.no_bids)} candles={len(candles)}")
        if (book.yes_bids or book.no_bids or candles) and not shown:
            shown = True
            if top_yes or top_no:
                print(f"    top_yes={top_yes} top_no={top_no}")
            if candles:
                last = candles[-1]
                print(f"    last candle: o={last.open} h={last.high} "
                      f"l={last.low} c={last.close} vol={last.volume}")
    if not shown:
        print("  - note: no resting liquidity in first markets (endpoints OK)")

    print("SMOKE OK: kalshi events+markets+orderbook+candles")
    sys.exit(0)


if __name__ == "__main__":
    main()
