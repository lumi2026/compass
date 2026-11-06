"""Prune dead market nodes (closed/settled/delisted on Kalshi).

Diffs graph markets against live open markets per event, then retires
the dead ones as one logged batch (replay-safe: pruned stays pruned).
Events are never retired — a listed event with no open markets keeps
its node and news links.

Stop the graph API first: it owns the in-memory store and would
overwrite this script's snapshot on its next batch. The script
refuses --apply while :8090 answers (override with --api-check "").

Run:  uv run python scripts/prune_dead.py            # dry run
      uv run python scripts/prune_dead.py --apply    # retire + save
"""

import json
import sys
import urllib.parse
import urllib.request

import click

sys.path.insert(0, ".")

from graph.persist import boot, commit_retire  # noqa: E402
from graph.derive import materialize_derived_nodes  # noqa: E402
from sources.kalshi import API_BASE  # noqa: E402


def _get_json(url: str, timeout: int = 30, retries: int = 4) -> dict:
    import time
    import urllib.error

    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "compass-hackathon/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429 or attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise last  # pragma: no cover


@click.command()
@click.option("--data-dir", default="data_local")
@click.option("--kalshi-base", default=API_BASE)
@click.option("--apply", is_flag=True, default=False,
              help="retire + save (default is a dry run)")
@click.option("--api-check", default="http://127.0.0.1:8090/health",
              help="refuse --apply while this answers (\"\" to skip)")
def main(data_dir, kalshi_base, apply, api_check) -> None:
    if apply and api_check:
        try:
            _get_json(api_check, timeout=5)
            raise SystemExit(
                "graph API is running — stop it first so its next "
                "batch cannot overwrite the prune (or pass --api-check \"\")")
        except SystemExit:
            raise
        except Exception:
            pass
    snap = f"{data_dir.rstrip('/')}/graph_snapshot.json"
    log = f"{data_dir.rstrip('/')}/graph_log.jsonl"
    api = boot(snap, log)
    markets = {i for i, n in api.store.nodes.items() if n.kind == "Market"}
    markets |= {n.id for n in materialize_derived_nodes(api.store)
                if n.kind == "Market"}
    markets = sorted(markets)
    events = sorted(i.removeprefix("kalshi:") for i in api.store.nodes
                    if i.startswith("kalshi:"))
    print(f"[prune] rev={api.revision} markets={len(markets)} "
          f"events={len(events)}", flush=True)
    live: set[str] = set()
    for e in events:
        q = urllib.parse.urlencode(
            {"event_ticker": e, "limit": 200, "status": "open"})
        try:
            d = _get_json(f"{kalshi_base.rstrip('/')}/markets?{q}")
        except Exception:
            print(f"[prune] WARN {e}: markets fetch failed, keeping",
                  flush=True)
            live.update(t for t in markets if t.startswith("market:" + e + "-"))
            continue
        live.update(m.get("ticker", "") for m in d.get("markets", []))
    graph_tickers = {t.removeprefix("market:") for t in markets}
    dead = sorted(f"market:{t}" for t in graph_tickers - live)
    orphans = sorted(t for t in dead
                     if not any(t.removeprefix("market:").startswith(e + "-")
                                for e in events))
    # orphan tickers match no known event prefix: check them directly —
    # a renamed series still trades, so only retire on a hard 404.
    keep = set()
    for oid in orphans:
        try:
            d = _get_json(f"{kalshi_base.rstrip('/')}/markets/{oid.removeprefix('market:')}")
            if d.get("ticker") or d.get("market", {}).get("ticker"):
                keep.add(oid)
        except Exception:
            pass
    dead = [d for d in dead if d not in keep]
    per: dict[str, int] = {}
    for d in dead:
        t = d.removeprefix("market:")
        ev = next((e for e in events if t.startswith(e + "-")), "?")
        per[ev] = per.get(ev, 0) + 1
    print(f"[prune] dead={len(dead)} kept-orphans={len(keep)}", flush=True)
    for e, c in sorted(per.items(), key=lambda kv: -kv[1])[:15]:
        print(f"[prune]   {c:4d}  {e}", flush=True)
    if not apply:
        print("[prune] dry run — pass --apply to retire", flush=True)
        return
    out = commit_retire(api, dead, log_path=log, snapshot_path=snap)
    dropped = sum(r["dropped_edges"] for r in out["retired"])
    print(f"[prune] retired {len(dead)} nodes, {dropped} edges, "
          f"rev={out['revision']}", flush=True)


if __name__ == "__main__":
    main()
