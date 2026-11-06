"""Backfill Kalshi display metadata onto existing Event nodes.

Fetches each event's category/series/sub_title from the public Kalshi
API and POSTs scout add-node proposals; the gate merges props into
existing nodes (duplicate -> refreshed), so this is idempotent.

Run:  uv run python scripts/backfill_event_meta.py
      uv run python scripts/backfill_event_meta.py --graph-url URL --batch 25
"""

import json
import sys
import urllib.request

import click

sys.path.insert(0, ".")

from sources.kalshi import API_BASE  # noqa: E402


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "compass/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-base", default=API_BASE)
@click.option("--batch", type=int, default=25,
              help="proposals per POST")
def main(graph_url, kalshi_base, batch) -> None:
    try:
        data = _get_json(graph_url.rstrip("/") + "/graph")
    except Exception as e:
        raise SystemExit(f"graph API unreachable at {graph_url}: {e}")
    tickers = sorted(
        {e["data"]["id"].removeprefix("kalshi:")
         for e in data.get("elements", [])
         if e.get("data", {}).get("id", "").startswith("kalshi:")})
    print(f"[backfill] {len(tickers)} events", flush=True)
    proposals = []
    skipped = []
    for ticker in tickers:
        try:
            ev = _get_json(f"{kalshi_base.rstrip('/')}/events/{ticker}").get("event", {})
        except Exception:
            skipped.append(ticker)
            continue
        if not ev.get("event_ticker"):
            skipped.append(ticker)
            continue
        props = {k: str(ev[k]) for k in ("category", "series_ticker", "sub_title") if ev.get(k)}
        detail: dict = {"kind": "Event", "title": ev.get("title") or ticker}
        if props:
            detail["props"] = props
        proposals.append({"agent": "scout", "action": "add-node",
                          "entity_id": f"kalshi:{ticker}",
                          "detail": json.dumps(detail), "confidence": 0.8})
    tallies: dict = {}
    rev = None
    for i in range(0, len(proposals), batch):
        posted = _post_json(graph_url.rstrip("/") + "/proposals",
                            {"proposals": proposals[i:i + batch]})
        rev = posted.get("revision", rev)
        for v in posted.get("verdicts", []):
            tallies[v.get("reason", "?")] = tallies.get(v.get("reason", "?"), 0) + 1
    print(f"[backfill] posted {len(proposals)} rev={rev} {tallies}", flush=True)
    if skipped:
        print(f"[backfill] skipped {len(skipped)} (delisted?): {skipped[:5]}", flush=True)


if __name__ == "__main__":
    main()
