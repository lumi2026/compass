"""Brain CLI shim: one goal per run over the service tools.

Run:  uv run python scripts/brain.py --goal "report rev, counts, \
          and challenged hypotheses"
"""

import datetime
import sys

import click

sys.path.insert(0, ".")

from squad.brain import build_registry, run_goal  # noqa: E402
from squad.models import resolve_model  # noqa: E402


@click.command()
@click.option("--goal", required=True, help="operator goal, one run")
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-url", default="http://127.0.0.1:8080")
@click.option("--model", default="auto")
@click.option("--max-iterations", type=int, default=10)
@click.option("--langsmith-project", default=None)
def main(goal, graph_url, kalshi_url, model, max_iterations,
         langsmith_project) -> None:
    from services.auth import load_dotenv  # noqa: E402
    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    load_dotenv()
    if model == "None":
        model = None
    if model is None:
        raise SystemExit("brain needs a model (no --model None mode)")
    model_obj = resolve_model(model, role="brain")
    if model_obj is None:
        raise SystemExit("no model resolved (missing key?)")
    registry = build_registry(graph_url, kalshi_url, model)
    ls = setup_langsmith(project=langsmith_project)
    print(f"[brain] langsmith={'on' if ls['enabled'] else 'off'} "
          f"project={ls['project']}", flush=True)
    with squad_span("brain-goal", {"goal": goal}) as span:
        out = run_goal(goal, registry, model_obj, max_iterations)
        span.outputs = {"ok": out["ok"],
                        "tool_calls": out["tool_calls"]}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    print(f"[brain] {stamp} {out}", flush=True)
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
