"""Autonomous next steps: follow-ups first, challenges re-scanned,
two quiet rounds trigger one deep dig, failures stay neutral."""

from squad.next_steps import apply_next_steps, compute_next_steps


def _summ(mode="co-mention", pairs=0, vetoed=0, followups=(),
          ok=True):
    return {"ok": ok, "mode": mode, "pairs": pairs, "vetoed": vetoed,
            "followups": len(followups), "followup_events": list(followups)}


def test_empty_recent_rotates():
    nxt = compute_next_steps([])
    assert nxt["mode_pin"] is None and nxt["event_filter"] == []
    assert nxt["deep"] is False and nxt["history_snaps"] == 1


def test_fresh_links_become_next_focus():
    fups = [{"event_ticker": "EB", "via": "co-mention"},
            {"event_ticker": "EA", "via": "co-mention"}]
    nxt = compute_next_steps([_summ(pairs=2, followups=fups)])
    assert nxt["event_filter"] == ["EB", "EA"]
    assert nxt["mode_pin"] == "co-mention"
    assert nxt["deep"] is False


def test_vetoed_cycle_rescans_same_mode_deep():
    nxt = compute_next_steps([_summ(mode="burst", pairs=1, vetoed=2)])
    assert nxt["mode_pin"] == "burst"
    assert nxt["deep"] is True and nxt["history_snaps"] == 8
    assert nxt["event_filter"] == []


def test_two_quiet_rounds_trigger_one_deep_round():
    recent = [_summ(pairs=0), _summ(mode="burst", pairs=0)]
    nxt = compute_next_steps(recent)
    assert nxt["deep"] is True and nxt["history_snaps"] == 8
    assert nxt["mode_pin"] is None  # rotation resumes after


def test_one_quiet_round_keeps_rotating():
    nxt = compute_next_steps([_summ(pairs=3),
                              _summ(mode="burst", pairs=0)])
    assert nxt["deep"] is False and nxt["mode_pin"] is None


def test_failed_cycle_is_neutral():
    recent = [_summ(pairs=0), {"ok": False, "reason": "kalshi down"},
              _summ(mode="burst", pairs=0)]
    nxt = compute_next_steps(recent)
    assert nxt["deep"] is True  # two completed quiet rounds around it
    only_failed = compute_next_steps([{"ok": False}])
    assert only_failed["deep"] is False


def test_followups_beat_quiet_streak():
    fups = [{"event_ticker": "EC", "via": "shared-subject"},
            {"event_ticker": "ED", "via": "shared-subject"}]
    recent = [_summ(pairs=0), _summ(pairs=0),
              _summ(pairs=1, followups=fups)]
    nxt = compute_next_steps(recent)
    assert nxt["event_filter"] == ["EC", "ED"]
    assert nxt["mode_pin"] == "shared-subject"


def test_operator_pins_always_win():
    engine = compute_next_steps(
        [_summ(pairs=1, followups=[{"event_ticker": "EB",
                                    "via": "co-mention"}])])
    cli_depth = {"history_snaps": 1, "history_hours": 48.0,
                 "history_stride_hours": 6.0}
    nxt = apply_next_steps("burst", ("EA",), cli_depth, engine)
    assert nxt["mode_pin"] == "burst"
    assert nxt["event_slice"] == ("EA",)
    assert nxt["history_snaps"] == 1  # no deep asked: CLI depth kept


def test_engine_depth_applies_only_on_deep():
    cli_depth = {"history_snaps": 2, "history_hours": 24.0,
                 "history_stride_hours": 3.0}
    deep_eng = compute_next_steps([_summ(pairs=0), _summ(pairs=0)])
    nxt = apply_next_steps(None, (), cli_depth, deep_eng)
    assert nxt["history_snaps"] == 8  # engine deep overrides CLI depth
    assert nxt["event_slice"] == ()


def _propose(recent, monkeypatch):
    import tools.news as N

    monkeypatch.setattr(N, "_RECENT", list(recent))
    out = N.news_tools("http://g", "http://k")[
        "news_propose_step"]["func"]()
    assert out["ok"] is True
    return out


def test_propose_followups_become_batch(monkeypatch):
    fups = [{"event_ticker": "EB", "via": "co-mention"},
            {"event_ticker": "EA", "via": "co-mention"}]
    out = _propose([_summ(pairs=2, followups=fups)], monkeypatch)
    assert out["step"]["tool"] == "news_run_batch"
    job = out["step"]["args"]["cycles"][0]
    assert job["event_filter"] == ["EB", "EA"]
    assert job["mode_pin"] == "co-mention"


def test_propose_challenged_rescans_deep(monkeypatch):
    out = _propose([_summ(mode="burst", pairs=1, vetoed=2)],
                   monkeypatch)
    assert out["step"] == {
        "tool": "news_run_cycle",
        "args": {"mode_pin": "burst", "history_snaps": 8,
                 "history_hours": 48.0, "history_stride_hours": 6.0}}


def test_propose_quiet_goes_deep_without_pin(monkeypatch):
    out = _propose([_summ(pairs=0), _summ(pairs=0)], monkeypatch)
    assert out["step"]["tool"] == "news_run_cycle"
    assert out["step"]["args"]["history_snaps"] == 8
    assert out["step"]["args"]["mode_pin"] == ""


def test_propose_defaults_to_plain_cycle(monkeypatch):
    out = _propose([_summ(pairs=3)], monkeypatch)
    assert out["step"] == {"tool": "news_run_cycle", "args": {}}
    out = _propose([], monkeypatch)
    assert out["step"] == {"tool": "news_run_cycle", "args": {}}


def test_single_followup_needs_a_partner(monkeypatch):
    import tools.news as N

    out = _propose(
        [_summ(pairs=1,
               followups=[{"event_ticker": "EB",
                           "via": "co-mention"}])], monkeypatch)
    assert out["step"] == {"tool": "news_run_cycle", "args": {}}
    assert any("needs a partner" in r for r in out["reasons"])
    propose = N.news_tools("http://g", "http://k")[
        "news_propose_step"]["func"]
    assert propose.__doc__ is not None


def test_discover_retries_transient_502(monkeypatch):
    import io
    import json
    import urllib.error

    import scripts.news_loop as nl

    calls = {"n": 0, "sleeps": []}
    monkeypatch.setattr(nl.time, "sleep",
                        lambda s: calls["sleeps"].append(s))

    class Resp:
        def __init__(self, payload):
            self._b = io.BytesIO(json.dumps(payload).encode())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=-1):
            return self._b.read(n)

    def fake(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(url, 502, "Bad Gateway",
                                         {}, None)
        return Resp({"events": []})

    monkeypatch.setattr(nl.urllib.request, "urlopen", fake)
    assert nl.discover_topics("http://k", ("EA",), limit=30) == []
    assert calls["n"] == 2
    assert calls["sleeps"] == [5]


def test_discover_clamps_limit_to_kalshi_max(monkeypatch):
    import scripts.news_loop as nl

    seen = {}
    monkeypatch.setattr(
        nl, "_graph_events",
        lambda url: {f"KX{i:04d}": f"T{i}" for i in range(500)})
    def fake_get(url, timeout=120):
        seen["url"] = url
        return {"events": []}

    monkeypatch.setattr(nl, "_get_json", fake_get)
    assert nl.discover_topics("http://k", (), limit=30,
                              graph_url="http://g") == []
    assert seen["url"].endswith("/events?limit=200")


def test_discover_gives_up_after_retry(monkeypatch):
    import urllib.error

    import scripts.news_loop as nl

    calls = {"n": 0}
    monkeypatch.setattr(nl.time, "sleep", lambda s: None)

    def fake(url, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)

    import pytest

    monkeypatch.setattr(nl.urllib.request, "urlopen", fake)
    with pytest.raises(urllib.error.HTTPError):
        nl.discover_topics("http://k", (), limit=1)
    assert calls["n"] == 2


def test_commit_summary_carries_followup_events(monkeypatch):
    from scripts import news_loop as nl

    from scripts.news_loop import loop_hypothesis_id

    hid = loop_hypothesis_id("EA", "EB", "co_mention")
    posted = {"revision": 7,
              "verdicts": [{"entity_id": hid, "committed": True,
                            "reason": "created"}],
              "derived_fresh": 1}
    monkeypatch.setattr(nl, "_post_json",
                        lambda url, payload, timeout=120: posted)
    ctx = {"all_props": [], "pairs": [
        {"a": "EA", "b": "EB", "mode": "co-mention", "urls": []}],
        "topics": [{"event_ticker": "EA", "title": "A"},
                   {"event_ticker": "EB", "title": "B"}],
        "graph_url": "http://x", "mode": "co-mention", "cycle": 0,
        "llm_calls": 0, "vetoed": 0}
    summary = nl.stage_commit(ctx)
    assert summary["followups"] == 2
    assert summary["followup_events"] == [
        {"event_ticker": "EA", "via": "co-mention"},
        {"event_ticker": "EB", "via": "co-mention"}]
