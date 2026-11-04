"""Standalone Kalshi feed service: history + live push for agents.

Run:  uv run python scripts/kalshi_service.py --event KXELONMARS-99 --port 8080
      uv run python scripts/kalshi_service.py --ticker KXELONMARS-99 --env prod

Graph publishing (odds -> living graph) is off unless wired:
      uv run python scripts/kalshi_service.py --port 8080 \
        --graph-url http://127.0.0.1:8090 [--sweep]
`--sweep` publishes one odds batch at startup (needs a non-empty
watchlist, e.g. from --event/--ticker or --seed). The runtime
`POST /publish/graph` endpoint still works for late wiring.

Auth (only needed for the live websocket; REST polling works without):
  export KALSHI_KEY_ID=... KALSHI_PRIVATE_KEY_PATH=...  # or KALSHI_PRIVATE_KEY
  Without keys the service listens via REST polling and says so on /health.

Agents then:
  curl localhost:8080/snapshot
  curl 'localhost:8080/history?ticker=T&start=..&end=..&interval=60'
  curl localhost:8080/stream            # SSE push
  curl -X POST localhost:8080/subscriptions -d '{"url":"http://agent/hook"}'
"""

import asyncio
import sys
import threading

import click

sys.path.insert(0, ".")

from services.feed_seed import load as load_seed
from services.feed_store import FeedStore
from services.kalshi_auth import load_credentials
from services.kalshi_listener import KalshiListener
from services.kalshi_server import DEFAULT_MOVE_THRESHOLD_CENTS, Service, serve


def wire_graph_publish(service, graph_url: str,
                       move_threshold_cents: int = DEFAULT_MOVE_THRESHOLD_CENTS,
                       sweep: bool = False) -> dict | None:
    """Apply startup graph-publish options; returns a sweep result if run.

    Empty graph_url leaves publishing disabled (the default). When a
    sweep is requested without a URL, return the same reason the HTTP
    endpoint would so the caller can log it instead of failing silently.
    """
    url = (graph_url or "").rstrip("/")
    if not url:
        if sweep:
            return {"published": False, "reason": "no graph_url"}
        return None
    service.set_graph_publish(url, move_threshold_cents)
    return service.sweep() if sweep else None


@click.command()
@click.option("--event", multiple=True, default=())
@click.option("--ticker", multiple=True, default=())
@click.option("--port", type=int, default=8080)
@click.option("--env", type=click.Choice(["demo", "prod"]), default="demo")
@click.option("--seed", default=None, help="frozen seed JSON for offline runs")
@click.option("--graph-url", default="",
              help="Graph API to publish odds to (enables live publish).")
@click.option("--move-threshold-cents", type=click.IntRange(min=0),
              default=DEFAULT_MOVE_THRESHOLD_CENTS, show_default=True,
              help="Price move that emits a Happening.")
@click.option("--sweep", is_flag=True, default=False,
              help="Publish one odds sweep at startup (needs tickers).")
def main(event, ticker, port, env, seed, graph_url,
         move_threshold_cents, sweep) -> None:
    graph_url = (graph_url or "").rstrip("/")
    if graph_url and not graph_url.startswith(("http://", "https://")):
        raise click.BadParameter(
            "must start with http:// or https://", param_hint="--graph-url")
    creds = load_credentials(env)
    store = FeedStore()
    listener = KalshiListener(creds)
    service = Service(listener, store)
    tickers = service.set_topics(list(event), list(ticker))
    if seed:
        tickers = sorted(set(tickers) | set(load_seed(seed, store)))
        listener.watch(tickers)
    print(f"watching {len(tickers)} tickers (live_ws={listener.live}): {tickers[:5]}")
    if graph_url or sweep:
        result = wire_graph_publish(service, graph_url,
                                    move_threshold_cents, sweep)
        if result is not None:
            print(f"[kalshi] startup sweep: {result}")

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_until_complete, args=(listener.run(),), daemon=True)
    thread.start()
    try:
        serve(service, port).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
    sys.exit(0)


if __name__ == "__main__":
    main()
