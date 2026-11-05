"""Compass as a service: brain-driven loop over Kalshi/graph/news tools.

Each cycle runs the internal STANDING_GOAL upkeep pass (scan graph
+ discover Kalshi, build via news cycles, close out). --loop
repeats it forever (Ctrl-C stops). Per-goal --max-iterations 0
means no cap: the model runs until it answers. The service loop
itself is unbounded unless --max-cycles stops it — same 0 = forever
convention as news_loop.

Long unattended runs: every cycle logs RSS and uptime.
--max-rss-mb / --max-runtime-hours make the loop exit cleanly
(status 0) once a threshold is crossed, so a supervisor (systemd,
s6, a shell loop) can restart it. A weekly restart is the expected
mode: --max-runtime-hours 168. Both default to 0 (disabled).

Run:
  uv run python scripts/run_compass.py --loop
  uv run python scripts/run_compass.py --loop --interval-min 10
  uv run python scripts/run_compass.py --loop --max-runtime-hours 168
"""

import datetime
import sys
import time

import click

sys.path.insert(0, ".")

from squad.brain import STANDING_GOAL, build_registry, run_goal  # noqa: E402


def log(msg: str) -> None:
    print(f"[brain] {msg}", flush=True)


def _rss_mb() -> float:
    """Resident set size, or 0.0 where /proc is unavailable."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def _uptime_hours(started: float) -> float:
    return (time.monotonic() - started) / 3600


def _restart_reason(started: float, max_rss_mb: float,
                    max_runtime_hours: float) -> str | None:
    """Clean-exit guard so a supervisor can recycle the process."""
    rss = _rss_mb()
    up_h = _uptime_hours(started)
    if max_rss_mb > 0 and rss >= max_rss_mb:
        return f"rss {rss:.1f}MB >= max_rss_mb={max_rss_mb:g}"
    if max_runtime_hours > 0 and up_h >= max_runtime_hours:
        return f"uptime {up_h:.2f}h >= max_runtime_hours={max_runtime_hours:g}"
    return None


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-url", default="http://127.0.0.1:8080")
@click.option("--model", default="auto",
              help="LLM model spec (auto|model-id); needs a configured key.")
@click.option("--max-iterations", type=int, default=0,
              help="Tool-call budget per goal; 0 = no cap (default).")
@click.option("--loop", is_flag=True, default=False,
              help="Repeat the goal forever; without it, run once.")
@click.option("--interval-min", type=float, default=1)
@click.option("--max-cycles", type=int, default=0,
              help="Stop after N service cycles (0 = run forever).")
@click.option("--max-rss-mb", type=float, default=0,
              help="Clean exit when RSS reaches this (0 = off).")
@click.option("--max-runtime-hours", type=float, default=0,
              help="Clean exit after this uptime, e.g. 168 = weekly "
                   "(0 = off).")
@click.option("--max-context-chars", type=int, default=160_000,
              help="Per-goal transcript budget (chars).")
@click.option("--langsmith-project", default=None)
def main(graph_url, kalshi_url, model, max_iterations, loop,
         interval_min, max_cycles, max_rss_mb, max_runtime_hours,
         max_context_chars, langsmith_project) -> None:
    from services.kalshi_auth import load_dotenv  # noqa: E402
    from squad.models import resolve_model  # noqa: E402
    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    started = time.monotonic()
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
    propose = registry["news_propose_step"]["func"]
    active_goal = STANDING_GOAL
    log(f"tools={len(registry)} max_iterations="
        f"{'unbounded' if max_iterations <= 0 else max_iterations}")
    cycle = 0
    while True:
        try:
            prop = propose()
            first = prop.get("step") if prop.get("ok") else None
        except Exception as e:  # proposing never stops the pass
            first, prop = None, {"ok": False,
                                 "error": f"{type(e).__name__}: {e}"}
        log(f"cycle={cycle} proposed first step: "
            f"{first or prop.get('error', prop)}")
        with squad_span("brain-goal",
                         {"cycle": cycle, "graph_url": graph_url,
                          "kalshi_url": kalshi_url, "model": model}) as span:
            try:
                summary = run_goal(active_goal, registry, model_obj,
                                   max_iterations=max_iterations,
                                   max_context_chars=max_context_chars,
                                   first_step=first)
            except Exception as e:  # keep the service alive
                summary = {"ok": False,
                           "reason": f"{type(e).__name__}: {e}"}
            span.outputs = summary
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        log(f"{stamp} cycle={cycle} {summary} "
            f"rss={_rss_mb():.1f}MB uptime={_uptime_hours(started):.2f}h")
        if not loop:
            log("done: single pass complete")
            sys.exit(0 if summary.get("ok") else 1)
        cycle += 1
        if max_cycles and cycle >= max_cycles:
            log(f"done: reached max_cycles={max_cycles}")
            sys.exit(0)
        reason = _restart_reason(started, max_rss_mb, max_runtime_hours)
        if reason:
            log(f"clean exit for supervisor restart: {reason}")
            sys.exit(0)
        wake = datetime.datetime.now(datetime.timezone.utc) + \
            datetime.timedelta(minutes=interval_min)
        log(f"cycle={cycle - 1} complete; next cycle={cycle} at "
            f"{wake.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
