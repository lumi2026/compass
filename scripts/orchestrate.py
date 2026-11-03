"""Supervisor over the news-loop subagents. No model reasoning here.

Each round the orchestrator decides the next cycle's parameters from
the previous round's outcome, then launches it through the news
loop's STAGES (discover -> scan -> pair -> propose -> commit).
Policies are plain rules, so behavior is reproducible and offline
testable; LLM spend stays exactly what the cycles themselves use.

Policies (v1):
- rotate modes; after a round that newly challenged a hypothesis,
  re-run the same mode with history depth (dig for better evidence);
- after two quiet rounds (no pairs), run one deep history round,
  then return to the base depth.

Run:  uv run python scripts/orchestrate.py --once
      uv run python scripts/orchestrate.py --loop --interval-min 60
"""

import datetime
import json
import sys
import time
import urllib.request

import click

sys.path.insert(0, ".")

from scripts import news_loop as nl  # noqa: E402


def log(msg: str) -> None:
    print(f"[orchestrate] {msg}", flush=True)


def _get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def challenged_hids(graph_url: str) -> set:
    """Hypothesis ids carrying an Audit challenge. Fail-open: empty."""
    try:
        data = _get_json(graph_url.rstrip("/") + "/graph")
    except Exception:
        return set()
    nodes = {}
    out = set()
    for el in data.get("elements", []):
        d = el.get("data", {})
        if "source" not in d:
            nodes[d.get("id")] = d
    for el in data.get("elements", []):
        d = el.get("data", {})
        if d.get("label") != "challenges":
            continue
        out.add(d.get("target", ""))
        src = nodes.get(d.get("source", ""), {})
        if src.get("props", {}).get("target"):
            out.add(src["props"]["target"])
    return {h for h in out if h}


def decide_cycle(state: dict, last: dict | None,
                 challenged_new: bool) -> dict:
    """Next cycle parameters from the previous round. Pure.

    state: {round, quiet_streak} (mutated: streak bookkeeping lives
    here so decide stays a pure function of its inputs).
    last: previous round's summary or None on the first round.
    """
    params: dict = {"mode_pin": None,
                    "history_snaps": state.get("history_base", 1),
                    "note": "rotate"}
    if last is None:
        return params
    if challenged_new:
        params["mode_pin"] = last.get("mode")
        params["history_snaps"] = 3
        params["note"] = (f"dig {last.get('mode')} deeper after "
                          f"fresh challenge")
        state["quiet_streak"] = 0
        return params
    if last.get("pairs", 0) == 0:
        state["quiet_streak"] = state.get("quiet_streak", 0) + 1
        if state["quiet_streak"] >= 2:
            params["history_snaps"] = 3
            params["note"] = "deep history after quiet streak"
            state["quiet_streak"] = 0
        else:
            params["note"] = "rotate (quiet x1)"
    else:
        state["quiet_streak"] = 0
    return params


def run_round(graph_url: str, kalshi_url: str, state: dict,
              model, event_limit: int,
              max_pairs: int, event_slice: tuple = ()) -> dict:
    """Decide, launch one news cycle through STAGES, react. Returns
    {summary, decision, next_params} (next_params stashed in state)."""
    before = challenged_hids(graph_url)
    params = state.pop("next_params", None)
    if params is None:
        params = decide_cycle(state, None, False)
    round_no = state.get("round", 0)
    ctx: dict = {
        "graph_url": graph_url, "kalshi_url": kalshi_url,
        "cycle": round_no, "event_slice": event_slice,
        "max_pairs": max_pairs,
        "mode": params["mode_pin"] or nl.next_mode(round_no, None),
        "model": model, "event_limit": event_limit,
        "history_snaps": params["history_snaps"],
        "history_hours": 48, "history_stride_hours": 6,
        "deliberate": False, "sync_topics": True,
    }
    log(f"round={round_no} {params['note']}")
    summary = None
    try:
        nl.run_stage("discover", ctx)
    except Exception as e:
        summary = {"ok": False, "reason": f"kalshi unreachable: {e}"}
    if summary is None and len(ctx["topics"]) < 2:
        summary = {"ok": False,
                   "reason": f"need 2+ topics, have {len(ctx['topics'])}"}
    if summary is None:
        try:
            nl.run_stage("scan", ctx)
        except RuntimeError as e:
            summary = {"ok": False, "reason": str(e)}
    if summary is None:
        gated = nl.run_stage("pair", ctx)
        if "done" in gated:
            summary = gated["done"]
        else:
            nl.run_stage("propose", ctx)
            summary = nl.run_stage("commit", ctx)
    fresh = challenged_hids(graph_url) - before
    if fresh:
        log(f"fresh challenges: {sorted(fresh)} (dig next round)")
    state["round"] = round_no + 1
    state["next_params"] = decide_cycle(state, summary, bool(fresh))
    return {"summary": summary, "decision": params,
            "next_params": state["next_params"]}


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-url", default="http://127.0.0.1:8080")
@click.option("--model", default="auto")
@click.option("--event-limit", type=int, default=30)
@click.option("--max-pairs", type=int, default=10,
              help="pairs per cycle, 0 = no cap")
@click.option("--history-base", type=int, default=1)
@click.option("--once", is_flag=True, default=False)
@click.option("--loop", is_flag=True, default=False)
@click.option("--max-cycles", type=int, default=0)
@click.option("--interval-min", type=float, default=60)
@click.option("--langsmith-project", default=None)
def main(graph_url, kalshi_url, model, event_limit, max_pairs,
         history_base, once, loop, max_cycles, interval_min,
         langsmith_project) -> None:
    from services.auth import load_dotenv  # noqa: E402
    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    load_dotenv()
    if model == "None":
        model = None
    ls = setup_langsmith(project=langsmith_project)
    log(f"langsmith={'on' if ls['enabled'] else 'off'} "
        f"project={ls['project']}")
    state: dict = {"round": 0, "quiet_streak": 0,
                   "history_base": history_base}
    while True:
        with squad_span("orchestrate-round",
                        {"round": state["round"]}) as span:
            try:
                out = run_round(graph_url, kalshi_url, state,
                                model, event_limit, max_pairs)
            except Exception as e:  # keep the loop alive; record and retry
                out = {"summary": {"ok": False,
                                   "reason": f"{type(e).__name__}: {e}"},
                       "decision": {"note": "error"},
                       "next_params": None}
            span.outputs = out["summary"]
        last = out["summary"]
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        print(f"[orchestrate] {stamp} round decision={out['decision']} "
              f"next={out['next_params']} summary={last}", flush=True)
        if not loop:
            sys.exit(0 if last.get("ok") else 1)
        if max_cycles and state["round"] >= max_cycles:
            sys.exit(0)
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
