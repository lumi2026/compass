"""Standalone Kalshi feed service: history + live push for agents.

Run:  uv run python scripts/kalshi_service.py --event KXELONMARS-99 --port 8080
      uv run python scripts/kalshi_service.py --ticker KXELONMARS-99 --env prod

Auth (only needed for the live websocket; REST polling works without):
  export KALSHI_KEY_ID=... KALSHI_PRIVATE_KEY_PATH=...  # or KALSHI_PRIVATE_KEY
  Without keys the service listens via REST polling and says so on /health.

Agents then:
  curl localhost:8080/snapshot
  curl 'localhost:8080/history?ticker=T&start=..&end=..&interval=60'
  curl localhost:8080/stream            # SSE push
  curl -X POST localhost:8080/subscriptions -d '{"url":"http://agent/hook"}'
"""

import argparse
import asyncio
import sys
import threading

sys.path.insert(0, ".")

from services.auth import load_credentials
from services.feed import FeedStore
from services.listener import KalshiListener
from services.seed import load as load_seed
from services.server import Service, serve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", action="append", default=[])
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--env", default="demo", choices=["demo", "prod"])
    parser.add_argument("--seed", default=None, help="frozen seed JSON for offline runs")
    args = parser.parse_args()

    creds = load_credentials(args.env)
    store = FeedStore()
    listener = KalshiListener(creds)
    service = Service(listener, store)
    tickers = service.set_topics(args.event, args.ticker)
    if args.seed:
        tickers = sorted(set(tickers) | set(load_seed(args.seed, store)))
        listener.watch(tickers)
    print(f"watching {len(tickers)} tickers (live_ws={listener.live}): {tickers[:5]}")

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_until_complete, args=(listener.run(),), daemon=True)
    thread.start()
    try:
        serve(service, args.port).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
