"""Model-driven brain over the service tools.

The model reasons over Kalshi/graph/news tool results and answers,
or the iteration budget stops it (max_iterations <= 0 = no cap).
Tools are plain functions (see tools/); writes go through the gated
proposals path only. STANDING_GOAL is the default upkeep pass
(scan graph + discover Kalshi, build via news cycles, close out);
scripts/run_compass.py repeats it as a service.
"""

import json

from langchain_core.messages import (AIMessage, HumanMessage,
                                     SystemMessage, ToolMessage)

from tools.graph import graph_tools
from tools.kalshi import kalshi_tools
from tools.news import news_tools


BRAIN_PROMPT = """You are the Compass brain (Kalshi prediction markets +
news interconnects). Achieve the operator's goal with the tools
below, then stop.

Protocol, every turn exactly one JSON object, no prose outside it:
{"tool": "<name>", "args": {...}} to call a tool, or
{"answer": "<final result for the operator>"} when done.

Rules: prefer read-only tools; launch news work through
news_run_cycle (one cycle) or news_run_batch (several cycles
concurrently, one per selected event set);
propose writes only through gated proposals; report tool errors
honestly instead of retrying blindly;
answer as soon as the goal is met — a correct short run beats a long
one. You have a fixed iteration budget, enforced outside; unspent
budget is fine."""

STANDING_GOAL = """Run one full Compass upkeep pass:

1. Snapshot the graph: graph_summary + graph_list_hypotheses.
   Record every existing kalshi: topic already graphed.
2. Discover Kalshi now: kalshi_service_status +
   kalshi_discover_events. Diff against the snapshot and name
   which open events are NEW (not yet graphed) vs already
   standing.
3. Search news for existing + new topics: kalshi_sync_watchlist
   to the chosen events, kalshi_trigger_sweep for newcomers,
   then fan out news work — one news_run_cycle for a narrow set,
   or news_run_batch with one entry per event set (distinct
   event_filter each) for wide coverage. Start with news_propose_step
   and execute the returned step first — the loop proposes its own
   next move (follow-up dig, challenge re-scan, deep round) from
   recent cycles. Then continue the pass.
   Each news agent builds the graph internally
   (discover->scan->pair->propose->commit). A failing news agent
   is not fatal: note it, ignore it, and continue with the rest.
4. Close out when all news agents complete: graph_trigger_derive,
   re-check graph_summary to confirm the revision advanced, post
   a short ops note with graph_post_finding, then answer with new
   events added, cycles run, and revision before/after. The outer
   service waits X minutes between passes; when current news is
   fully scanned, stride back in time (history_snaps > 1 with
   larger history_hours / history_stride_hours) instead of
   rescanning the same window."""


def build_registry(graph_url: str, kalshi_url: str,
                   model: str | None = "auto") -> dict:
    """Kalshi + graph + news_loop launch tools bound to live services."""
    registry = {}
    registry.update(kalshi_tools(kalshi_url))
    registry.update(graph_tools(graph_url))
    registry.update(news_tools(graph_url, kalshi_url, model))
    return registry


def _extract_json(raw: str) -> dict:
    """First {...} span in the reply (models wrap JSON in prose)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    obj = json.loads(raw[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON must be an object")
    return obj


def _execute(registry: dict, name, args) -> tuple:
    """Run one tool call. Returns (result, counted).

    Unknown tools and malformed args are feedback, not calls — the
    model is not charged for them.
    """
    spec = registry.get(name)
    if spec is None:
        return ({"ok": False,
                 "error": f"unknown tool: {name} "
                          f"(have: {sorted(registry)})"}, False)
    if not isinstance(args, dict):
        return ({"ok": False, "error": "args must be an object"}, False)
    try:
        return (spec["func"](**args), True)
    except TypeError as e:
        return ({"ok": False, "error": f"bad args: {e}"}, True)
    except Exception as e:
        return ({"ok": False,
                 "error": f"{type(e).__name__}: {e}"}, True)


def run_goal(goal: str, registry: dict, model_obj,
             max_iterations: int = 10, first_step: dict | None = None) -> dict:
    """Drive one goal to an answer or the budget. Returns
    {ok, answer?, tool_calls, iterations, reason?}.

    max_iterations <= 0 means no cap: run until the model answers
    (Ctrl-C stops the service loop outside). `first_step` is an
    already-decided {"tool", "args"} (e.g. from news_propose_step):
    it executes before the model reasons, and the transcript shows it
    as the opening action, so the brain starts from there.
    """
    transcript = [SystemMessage(BRAIN_PROMPT), HumanMessage(goal)]
    tool_calls = 0
    i = 0
    if isinstance(first_step, dict) and first_step.get("tool"):
        name, args = first_step.get("tool"), first_step.get("args", {})
        result, counted = _execute(registry, name, args)
        if counted:
            tool_calls += 1
        call_id = "sup-first"
        raw_first = json.dumps({"tool": name, "args": args or {}})
        transcript += [
            AIMessage(content=raw_first,
                      tool_calls=[{"name": name, "args": args or {},
                                   "id": call_id, "type": "tool_call"}]),
            ToolMessage(content=json.dumps(result), tool_call_id=call_id)]
    while True:
        if max_iterations > 0 and i >= max_iterations:
            break
        try:
            msg = model_obj.invoke(transcript)
            raw = msg.content if isinstance(msg.content, str) else str(
                msg.content)
        except Exception as e:
            return {"ok": False, "tool_calls": tool_calls, "iterations": i,
                    "reason": f"model error: {type(e).__name__}: {e}"}
        try:
            act = _extract_json(raw)
        except ValueError as e:
            transcript += [AIMessage(raw), HumanMessage(
                f"Protocol error ({e}): reply with one JSON object, "
                '{"tool": name, "args": {...}} or {"answer": ...}.')]
            i += 1
            continue
        if "answer" in act:
            return {"ok": True, "answer": act["answer"],
                    "tool_calls": tool_calls, "iterations": i + 1}
        name, args = act.get("tool"), act.get("args", {})
        result, counted = _execute(registry, name, args)
        if counted:
            tool_calls += 1
        call_id = f"sup{i}"
        transcript += [
            AIMessage(content=raw,
                      tool_calls=[{"name": name, "args": args or {},
                                   "id": call_id, "type": "tool_call"}]),
            ToolMessage(content=json.dumps(result), tool_call_id=call_id)]
        i += 1
    return {"ok": False, "tool_calls": tool_calls,
            "iterations": i,
            "reason": "iteration budget exhausted"}
