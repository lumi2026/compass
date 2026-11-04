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
    fups = [{"event_ticker": "EC", "via": "shared-subject"}]
    recent = [_summ(pairs=0), _summ(pairs=0),
              _summ(pairs=1, followups=fups)]
    nxt = compute_next_steps(recent)
    assert nxt["event_filter"] == ["EC"]
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


def test_commit_summary_carries_followup_events(monkeypatch):
    from scripts import news_loop as nl

    from scripts.news_loop import _slug

    hid = f"hypothesis:loop-{_slug('EA')}-{_slug('EB')}"
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
