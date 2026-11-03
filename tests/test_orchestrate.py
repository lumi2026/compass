"""Orchestrator: decisions pure, rounds stubbed. No network."""

import scripts.orchestrate as orch
from scripts.orchestrate import challenged_hids, decide_cycle


def _els():
    return [
        {"data": {"id": "hypothesis:h1", "kind": "Hypothesis",
                  "label": "h1"}},
        {"data": {"id": "audit:a1", "kind": "Audit",
                  "label": "flags", "props": {"target": "hypothesis:h1"}}},
        {"data": {"source": "audit:a1", "target": "hypothesis:h1",
                  "label": "challenges"}},
        {"data": {"id": "kalshi:A", "kind": "Event", "label": "A"}},
    ]


def test_challenged_hids_from_edge_and_props(monkeypatch):
    monkeypatch.setattr(
        orch, "_get_json", lambda url, timeout=30: {"elements": _els()})
    assert challenged_hids("http://x") == {"hypothesis:h1"}


def test_challenged_hids_fail_open(monkeypatch):
    def boom(url, timeout=30):
        raise RuntimeError("down")
    monkeypatch.setattr(orch, "_get_json", boom)
    assert challenged_hids("http://x") == set()


def test_decide_first_round_rotates():
    state = {"round": 0, "quiet_streak": 0, "history_base": 1}
    p = decide_cycle(state, None, False)
    assert p == {"mode_pin": None, "history_snaps": 1, "note": "rotate"}


def test_decide_fresh_challenge_digs_same_mode():
    state = {"round": 3, "quiet_streak": 2, "history_base": 1}
    p = decide_cycle(state, {"ok": True, "mode": "burst", "pairs": 2},
                     True)
    assert p["mode_pin"] == "burst"
    assert p["history_snaps"] == 3
    assert state["quiet_streak"] == 0


def test_decide_quiet_twice_deepens_once():
    state = {"round": 1, "quiet_streak": 0, "history_base": 1}
    p1 = decide_cycle(state, {"ok": True, "pairs": 0}, False)
    assert p1["history_snaps"] == 1 and "quiet x1" in p1["note"]
    p2 = decide_cycle(state, {"ok": True, "pairs": 0}, False)
    assert p2["history_snaps"] == 3
    assert state["quiet_streak"] == 0


def _fake_stages(calls):
    def inner(name, ctx):
        calls.append(name)
        if name == "discover":
            return {"topics": [
                {"event_ticker": "A", "title": "A", "terms": ["t1"]},
                {"event_ticker": "B", "title": "B", "terms": ["t2"]}],
                "watch": {}}
        if name == "scan":
            return {"mentions": {}, "snap_ts": "t",
                    "burst_new": {}, "burst_old": {}}
        if name == "pair":
            return {"pairs": [{"a": "A", "b": "B", "urls": [],
                               "mode": "co-mention"}]}
        if name == "propose":
            return {"all_props": [], "pairs": ctx["pairs"],
                    "vetoed": 0, "llm_calls": 0}
        if name == "commit":
            return {"ok": True, "mode": "co-mention", "pairs": 1,
                    "proposals": 0, "revision": 9, "derived_fresh": 0,
                    "followups": 0, "llm_calls": 0, "vetoed": 0}
        raise AssertionError(name)

    def run_stage(name, ctx):
        res = inner(name, ctx)
        ctx.update(res)  # mirror the real run_stage merge contract
        return res
    return run_stage


def test_run_round_fresh_challenge_schedules_dig(monkeypatch):
    import scripts.news_loop as nl

    calls = []
    monkeypatch.setattr(nl, "run_stage", _fake_stages(calls))
    seen = iter([set(), {"hypothesis:h1"}])
    monkeypatch.setattr(
        orch, "challenged_hids", lambda url: next(seen))
    state = {"round": 0, "quiet_streak": 0, "history_base": 1}
    out = orch.run_round("http://g", "http://k", state, None, 10, 10)
    assert calls == ["discover", "scan", "pair", "propose", "commit"]
    assert out["summary"]["ok"] is True
    assert state["round"] == 1
    nxt = state["next_params"]
    assert nxt["mode_pin"] == "co-mention"
    assert nxt["history_snaps"] == 3


def test_run_round_stages_in_order_on_quiet(monkeypatch):
    import scripts.news_loop as nl

    def run_stage(name, ctx):
        if name == "discover":
            res = {"topics": [], "watch": {}}
            ctx.update(res)
            return res
        raise AssertionError(f"should stop before {name}")
    monkeypatch.setattr(nl, "run_stage", run_stage)
    monkeypatch.setattr(orch, "challenged_hids", lambda url: set())
    state = {"round": 0, "quiet_streak": 0, "history_base": 1}
    out = orch.run_round("http://g", "http://k", state, None, 10, 10)
    assert out["summary"] == {"ok": False,
                              "reason": "need 2+ topics, have 0"}
