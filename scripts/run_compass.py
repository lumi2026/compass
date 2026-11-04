"""Compass as a service: brain-driven loop over Kalshi/graph/news tools.

Each cycle runs the internal STANDING_GOAL upkeep pass (scan graph
+ discover Kalshi, build via news cycles, close out). --loop
repeats it forever (Ctrl-C stops). Per-goal --max-iterations 0
means no cap: the model runs until it answers. The service loop
itself is unbounded unless --max-cycles stops it — same 0 = forever
convention as news_loop.

Run:
  uv run python scripts/run_compass.py --loop
  uv run python scripts/run_compass.py --loop --interval-min 10
"""

import datetime
import sys
import time

import click

sys.path.insert(0, ".")

from squad.brain import STANDING_GOAL, build_registry, run_goal  # noqa: E402


def log(msg: str) -> None:
    print(f"[brain] {msg}", flush=True)


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-url", default="http://127.0.0.1:8080")
@click.option("--model", default="auto",
              help="LLM model spec (auto|model-id); needs a configured key.")
@click.option("--max-iterations", type=int, default=0,
              help="Tool-call budget per goal; 0 = no cap (default).")
@click.option("--loop", is_flag=True, default=False,
              help="Repeat the goal forever; without it, run once.")
@click.option("--interval-min", type=float, default=15)
@click.option("--max-cycles", type=int, default=0,
              help="Stop after N service cycles (0 = run forever).")
@click.option("--langsmith-project", default=None)
def main(graph_url, kalshi_url, model, max_iterations, loop,
         interval_min, max_cycles, langsmith_project) -> None:
    from services.kalshi_auth import load_dotenv  # noqa: E402
    from squad.models import resolve_model  # noqa: E402
    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    load_dotenv()
    ls = setup_langsmith(project=langsmith_project)
    log(f"langsmith={'on' if ls['enabled'] else 'off'} "
        f"project={ls['project']} endpoint={ls['endpoint']}")
    if not ls["enabled"]:
        log("tracing off: set LANGSMITH_TRACING=true plus "
            "LANGSMITH_API_KEY to enable brain traces")
    if model == "None":
        log("FAIL: brain is model-driven; --model None has no fallback.")
        sys.exit(1)
    model_obj = resolve_model(model, role="brain")
    if model_obj is None:
        log("FAIL: no model key configured (MODEL_API_KEY / provider key).")
        sys.exit(1)
    registry = build_registry(graph_url, kalshi_url, model)
    active_goal = STANDING_GOAL
    log(f"tools={len(registry)} max_iterations="
        f"{'unbounded' if max_iterations <= 0 else max_iterations}")
    cycle = 0
    while True:
        with squad_span("brain-goal",
                         {"cycle": cycle, "graph_url": graph_url,
                          "kalshi_url": kalshi_url, "model": model}) as span:
            try:
                summary = run_goal(active_goal, registry, model_obj,
                                   max_iterations=max_iterations)
            except Exception as e:  # keep the service alive
                summary = {"ok": False,
                           "reason": f"{type(e).__name__}: {e}"}
            span.outputs = summary
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        log(f"{stamp} cycle={cycle} {summary}")
        if not loop:
            log("done: single pass complete")
            sys.exit(0 if summary.get("ok") else 1)
        cycle += 1
        if max_cycles and cycle >= max_cycles:
            log(f"done: reached max_cycles={max_cycles}")
            sys.exit(0)
        wake = datetime.datetime.now(datetime.timezone.utc) + \
            datetime.timedelta(minutes=interval_min)
        log(f"cycle={cycle - 1} complete; next cycle={cycle} at "
            f"{wake.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
