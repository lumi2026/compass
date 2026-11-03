"""Interconnect demo: seed 3 topics, run the news team, scrub time.

Drives the live stack (graph API + kalshi service + news loop cycles)
and prints the evidence trail so the view can replay it:

  uv run python scripts/demo_interconnect.py --event KXWARSHOUT-26JUL \\
      --event KXSPACEXMARS-30 --event KXGDPQ3-25 --cycles 2

With no --event flags the script discovers the first --event-limit
events from the kalshi service instead. Each cycle runs one
exploration mode through run_cycle, then the script reports the
graph revision, counts, and newly created Hypothesis ids, plus a
before/after ?at= scrub pair for the time slider.
"""

import datetime
import json
import sys
import urllib.parse
import urllib.request

import click

sys.path.insert(0, ".")

from scripts.news_loop import discover_topics, run_cycle  # noqa: E402


def _get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _graph_summary(graph_url: str, at: str | None = None) -> dict:
    url = graph_url.rstrip("/") + "/graph"
    if at:
        url += "?at=" + urllib.parse.quote(at, safe="")
    data = _get_json(url)
    elements = data.get("elements", [])
    hyps = sorted(
        e["data"]["id"]
        for e in elements
        if e.get("data", {}).get("kind") == "Hypothesis"
    )
    return {
        "revision": data.get("revision"),
        "nodes": data.get("nodes_count", len(elements)),
        "edges": data.get("edges_count"),
        "hypotheses": hyps,
    }


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-url", default="http://127.0.0.1:8080")
@click.option("--event", multiple=True, default=(),
              help="event tickers to demo (repeatable)")
@click.option("--event-limit", type=int, default=3,
              help="events to discover when no --event is given")
@click.option("--cycles", type=int, default=2,
              help="news-team cycles to run (rotates modes)")
@click.option("--max-pairs", type=int, default=10)
@click.option("--model", default="auto",
              help="LLM model spec (auto|None|model-id); None = extractive")
def main(graph_url, kalshi_url, event, event_limit, cycles,
         max_pairs, model) -> None:
    start = datetime.datetime.now(datetime.timezone.utc)
    start_ts = start.isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        before = _graph_summary(graph_url)
    except Exception as e:
        raise SystemExit(
            f"graph API unreachable at {graph_url} "
            f"(start it first: uv run python -m services.graph_server "
            f"--port 8090): {e}")
    tickers = tuple(event)
    if not tickers:
        try:
            topics = discover_topics(kalshi_url, (), limit=event_limit)
        except Exception as e:
            raise SystemExit(
                f"kalshi service unreachable at {kalshi_url} "
                f"(start it first, then retry): {e}")
        tickers = tuple(t["event_ticker"] for t in topics)
        if not tickers:
            raise SystemExit("kalshi discovery returned no usable topics")
    print(f"[demo] topics={list(tickers)} rev_before={before['revision']}",
          flush=True)
    for cycle in range(cycles):
        summary = run_cycle(
            graph_url, kalshi_url, cycle=cycle, event_slice=tickers,
            max_pairs=max_pairs, model=model, event_limit=len(tickers))
        print(f"[demo] cycle={cycle} mode={summary.get('mode')} "
              f"pairs={summary.get('pairs', 0)} "
              f"proposals={summary.get('proposals', 0)} "
              f"rev={summary.get('revision', before['revision'])} "
              f"followups={summary.get('followups', 0)} "
              f"llm_calls={summary.get('llm_calls', 0)} "
              f"vetoed={summary.get('vetoed', 0)}", flush=True)
    after = _graph_summary(graph_url)
    new_hyps = [h for h in after["hypotheses"] if h not in before["hypotheses"]]
    print(f"[demo] rev {before['revision']} -> {after['revision']}, "
          f"nodes={after['nodes']} edges={after['edges']}", flush=True)
    print(f"[demo] new hypotheses ({len(new_hyps)}): {new_hyps}", flush=True)
    scrub_before = _graph_summary(graph_url, at=start_ts)
    print(f"[demo] scrub ?at={start_ts}: "
          f"rev={scrub_before['revision']} "
          f"nodes={scrub_before['nodes']} "
          f"(odds stay live until replay lands)", flush=True)
    print(f"[demo] open the view and scrub from {start_ts} to now "
          f"to watch the interconnect arrive", flush=True)


if __name__ == "__main__":
    main()
