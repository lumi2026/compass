"""News-loop subagent tools (deterministic STAGES cycles)."""

from __future__ import annotations

import concurrent.futures
import json


def news_tools(graph_url: str, kalshi_url: str,
               model: str | None = "auto") -> dict:
    """{tool_name: {description, func}} launching news_loop cycles."""

    def _run_one(job: dict, cycle: int) -> dict:
        from scripts import news_loop as nl

        mode_pin = str(job.get("mode_pin", "") or "").strip() or None
        if mode_pin is not None and mode_pin not in nl.MODES:
            return {"ok": False,
                    "error": f"unknown mode: {mode_pin} "
                             f"(want one of {nl.MODES})"}
        raw_filter = job.get("event_filter", "")
        if isinstance(raw_filter, list):
            event_slice = tuple(t for t in raw_filter if t)
        else:
            event_slice = tuple(
                t.strip() for t in str(raw_filter or "").split(",")
                if t.strip())
        try:
            return nl.run_cycle(
                graph_url, kalshi_url, cycle=cycle,
                event_slice=event_slice,
                max_pairs=int(job.get("max_pairs", 10)),
                mode_pin=mode_pin, model=model,
                event_limit=int(job.get("event_limit", 30)),
                history_snaps=int(job.get("history_snaps", 1)),
                history_hours=float(job.get("history_hours", 48)),
                history_stride_hours=float(
                    job.get("history_stride_hours", 6)))
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def run_news_cycle(mode_pin: str = "",
                       event_limit: int = 30,
                       max_pairs: int = 10,
                       history_snaps: int = 1,
                       history_hours: float = 48,
                       history_stride_hours: float = 6,
                       event_filter: str = "") -> dict:
        """Run one news_loop subagent cycle (discover->scan->pair->propose->commit)."""
        try:
            from scripts import news_loop as nl  # noqa: F401
        except Exception as e:
            return {"ok": False, "error": f"import: {e}"}
        return _run_one({"mode_pin": mode_pin, "event_limit": event_limit,
                         "max_pairs": max_pairs,
                         "history_snaps": history_snaps,
                         "history_hours": history_hours,
                         "history_stride_hours": history_stride_hours,
                         "event_filter": event_filter}, 0)

    def run_news_batch(cycles_json: str | list = "",
                       cycles: list | None = None,
                       max_workers: int = 4) -> dict:
        """Launch several news_loop subagent cycles concurrently.

        The job list is per-cycle {mode_pin, event_limit,
        max_pairs, history_snaps, history_hours,
        history_stride_hours, event_filter} objects, one per
        selected event set. Pass it as a JSON string (cycles_json)
        or a plain list (cycles, or cycles_json as a list).
        Results come back in input order.
        """
        jobs = cycles if isinstance(cycles, list) else cycles_json
        if isinstance(jobs, str):
            try:
                jobs = json.loads(jobs)
            except Exception as e:
                return {"ok": False, "error": f"bad cycles_json: {e}"}
        if not isinstance(jobs, list) or not jobs:
            return {"ok": False, "error": "cycles_json must be a non-empty list"}
        if any(not isinstance(j, dict) for j in jobs):
            return {"ok": False, "error": "each cycle must be an object"}
        workers = max(1, min(int(max_workers or 1), len(jobs)))
        try:
            from scripts import news_loop as nl  # noqa: F401
        except Exception as e:
            return {"ok": False, "error": f"import: {e}"}
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers) as ex:
            futs = [ex.submit(_run_one, job, i)
                    for i, job in enumerate(jobs)]
            results = [f.result() for f in futs]
        return {"ok": all(r.get("ok") for r in results),
                "results": results}

    return {
        "news_run_cycle": {
            "description": "Launch one news_loop subagent cycle "
                           "(discover->scan->pair->propose->commit).",
            "func": run_news_cycle},
        "news_run_batch": {
            "description": "Launch several news_loop subagent cycles "
                           "concurrently over selected event sets "
                           "(JSON list); results in input order.",
            "func": run_news_batch},
    }
