"""Bounded model-driven brain over the service tools.

One goal per run: the model reasons over Kalshi/graph/news tool
results and answers, or the iteration budget stops it. Tools are
plain functions (see tools/); writes go through the gated proposals
path only. No loop flag in v1 — schedule one goal per invocation.
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


def run_goal(goal: str, registry: dict, model_obj,
             max_iterations: int = 10) -> dict:
    """Drive one goal to an answer or the budget. Returns
    {ok, answer?, tool_calls, iterations, reason?}."""
    transcript = [SystemMessage(BRAIN_PROMPT), HumanMessage(goal)]
    tool_calls = 0
    for i in range(max(1, max_iterations)):
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
            continue
        if "answer" in act:
            return {"ok": True, "answer": act["answer"],
                    "tool_calls": tool_calls, "iterations": i + 1}
        name, args = act.get("tool"), act.get("args", {})
        spec = registry.get(name)
        if spec is None:
            result = {"ok": False,
                      "error": f"unknown tool: {name} "
                               f"(have: {sorted(registry)})"}
        elif not isinstance(args, dict):
            result = {"ok": False, "error": "args must be an object"}
        else:
            tool_calls += 1
            try:
                result = spec["func"](**args)
            except TypeError as e:
                result = {"ok": False, "error": f"bad args: {e}"}
            except Exception as e:
                result = {"ok": False,
                          "error": f"{type(e).__name__}: {e}"}
        call_id = f"sup{i}"
        transcript += [
            AIMessage(content=raw,
                      tool_calls=[{"name": name, "args": args or {},
                                   "id": call_id, "type": "tool_call"}]),
            ToolMessage(content=json.dumps(result), tool_call_id=call_id)]
    return {"ok": False, "tool_calls": tool_calls,
            "iterations": max(1, max_iterations),
            "reason": "iteration budget exhausted"}
