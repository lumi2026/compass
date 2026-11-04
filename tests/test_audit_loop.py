"""Independent audit loop: selection, veto posts, state. No network."""

import json
from types import SimpleNamespace

from scripts.audit_loop import (audit_pass, load_state, run_once, save_state,
                                select_candidates, select_vetoed)


def N(i, kind, label=None, props=None):
    d = {"id": i, "kind": kind, "label": label or i}
    if props:
        d["props"] = props
    return {"data": d}


def E(src, dst, label):
    return {"data": {"source": src, "target": dst, "label": label}}


def _els():
    return [
        N("kalshi:A", "Event", "A"),
        N("kalshi:B", "Event", "B"),
        N("kalshi:C", "Event", "C"),
        N("article:1", "Article", "shared story"),
        N("hypothesis:h1", "Hypothesis", "A x B"),
        N("hypothesis:h2", "Hypothesis", "B x C"),
        N("hypothesis:h3", "Hypothesis", "A x C"),
        N("audit:old", "Audit", "flags h3",
          {"target": "hypothesis:h3"}),
        E("hypothesis:h1", "kalshi:A", "supports"),
        E("hypothesis:h1", "kalshi:B", "supports"),
        E("hypothesis:h1", "article:1", "supports"),
        E("hypothesis:h2", "kalshi:B", "supports"),
        E("hypothesis:h2", "kalshi:C", "supports"),
        E("hypothesis:h3", "kalshi:A", "supports"),
        E("hypothesis:h3", "kalshi:C", "supports"),
        E("audit:old", "hypothesis:h3", "challenges"),
    ]


def test_select_skips_challenged_and_known():
    cands = select_candidates(
        _els(), {"hypothesis:h2": {"verdict": "keep"}}, 0)
    assert [c["hid"] for c in cands] == ["hypothesis:h1"]
    assert cands[0]["events"] == [
        {"ticker": "A", "title": "A"},
        {"ticker": "B", "title": "B"}]
    assert cands[0]["articles"] == ["shared story"]


def test_select_caps_and_sorts():
    els = _els()
    cands = select_candidates(els, {}, 1)
    assert [c["hid"] for c in cands] == ["hypothesis:h1"]


def test_audit_pass_keep_writes_no_posts():
    calls = []
    res = audit_pass("http://x", select_candidates(_els(), {}, 1), None,
                     post_fn=lambda u, p: calls.append(p) or {})
    assert (res["kept"], res["vetoed"], res["posted"]) == (1, 0, 0)
    assert calls == []
    assert res["verdicts"]["hypothesis:h1"]["verdict"] == "keep"


class _VetoModel:
    def invoke(self, msgs):
        return SimpleNamespace(content="NO\nmexico only\n0.9")


def test_audit_pass_veto_posts_challenge():
    calls = []

    def post(url, payload):
        calls.append(payload)
        return {"verdicts": [{"committed": True}, {"committed": True}]}

    res = audit_pass("http://x", select_candidates(_els(), {}, 1),
                     _VetoModel(), post_fn=post)
    assert (res["kept"], res["vetoed"], res["posted"]) == (0, 1, 2)
    props = calls[0]["proposals"]
    assert props[0]["agent"] == "auditor"
    node = json.loads(props[0]["detail"])
    assert node["kind"] == "Audit"
    assert node["props"]["target"] == "hypothesis:h1"
    edge = json.loads(props[1]["detail"])
    assert (edge["src"], edge["dst"], edge["rel"]) == (
        "audit:hypothesis:h1", "hypothesis:h1", "challenges")


def test_audit_pass_dry_run_posts_nothing():
    calls = []
    res = audit_pass("http://x", select_candidates(_els(), {}, 1),
                     _VetoModel(), dry_run=True,
                     post_fn=lambda u, p: calls.append(p) or {})
    assert res["vetoed"] == 1 and res["posted"] == 0 and calls == []


def test_select_skips_hypotheses_without_two_events():
    els = _els() + [
        N("hypothesis:h4", "Hypothesis", "A alone"),
        E("hypothesis:h4", "kalshi:A", "supports"),
    ]
    cands = select_candidates(els, {}, 0)
    assert [c["hid"] for c in cands] == ["hypothesis:h1", "hypothesis:h2"]


def test_run_once_refuses_missing_model(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    monkeypatch.setattr(loop, "resolve_model", lambda *a, **k: None)
    state = str(tmp_path / "audit_state.json")
    out = run_once("http://x", "auto", 0, False, state)
    assert out["ok"] is False and "model" in out["reason"]
    assert load_state(state) == {}


class _BoomModel:
    def invoke(self, msgs):
        raise RuntimeError("model down")


def test_run_once_retries_llm_errors(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    monkeypatch.setattr(
        loop, "resolve_model", lambda *a, **k: _BoomModel())
    state = str(tmp_path / "audit_state.json")
    out = run_once("http://x", "auto", 1, False, state)
    assert out["ok"] is True and out["kept"] == 1
    assert out["recorded"] == 0
    assert load_state(state) == {}


def test_run_once_retries_uncommitted_challenge(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    monkeypatch.setattr(
        loop, "resolve_model", lambda *a, **k: _VetoModel())
    monkeypatch.setattr(
        loop, "_post_json",
        lambda url, payload, timeout=30: {
            "verdicts": [{"committed": False}, {"committed": False}]})
    state = str(tmp_path / "audit_state.json")
    out = run_once("http://x", "auto", 1, False, state)
    assert out["vetoed"] == 1 and out["posted"] == 0
    assert out["recorded"] == 0
    assert load_state(state) == {}


def test_state_roundtrip(tmp_path):
    p = str(tmp_path / "audit_state.json")
    assert load_state(p) == {}
    assert load_state(str(tmp_path / "missing.json")) == {}
    save_state(p, {"h": {"verdict": "keep"}})
    assert load_state(p) == {"h": {"verdict": "keep"}}


def test_run_once_persists_keeps(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    state = str(tmp_path / "audit_state.json")
    out = run_once("http://x", None, 0, False, state)
    assert out["ok"] is True
    assert (out["audited"], out["kept"], out["vetoed"], out["posted"],
            out["llm_calls"]) == (2, 2, 0, 0, 0)
    saved = load_state(state)
    assert set(saved) == {"hypothesis:h1", "hypothesis:h2"}
    # second pass finds nothing new
    out2 = run_once("http://x", None, 0, False, state)
    assert out2["audited"] == 0


def _ok_post(url, payload):
    return {"verdicts": [{"committed": True}, {"committed": True}]}


def test_audit_pass_prune_removes_vetoed():
    retired = []

    def retire(url, payload):
        retired.append((url, payload))
        return {"retired": [{"entity_id": "hypothesis:h1",
                             "removed": True, "dropped_edges": 3}]}

    res = audit_pass("http://x", select_candidates(_els(), {}, 1),
                     _VetoModel(), post_fn=_ok_post,
                     prune=True, retire_fn=retire)
    assert res["vetoed"] == 1 and res["pruned"] == 1
    assert retired == [("http://x/retire",
                        {"node_ids": ["hypothesis:h1"]})]
    assert res["verdicts"]["hypothesis:h1"]["pruned"] is True


def test_audit_pass_prune_off_by_default_and_skipped_on_dry_run():
    calls = []
    res = audit_pass("http://x", select_candidates(_els(), {}, 1),
                     _VetoModel(), post_fn=_ok_post,
                     retire_fn=lambda u, p: calls.append((u, p)))
    assert res["pruned"] == 0 and calls == []
    res = audit_pass("http://x", select_candidates(_els(), {}, 1),
                     _VetoModel(), dry_run=True,
                     post_fn=_ok_post, prune=True,
                     retire_fn=lambda u, p: calls.append((u, p)))
    assert res["pruned"] == 0 and calls == []


def test_audit_pass_prune_skipped_when_challenge_transient():
    calls = []

    def bad_post(url, payload):
        return {"verdicts": [{"committed": False},
                             {"committed": False}]}

    res = audit_pass("http://x", select_candidates(_els(), {}, 1),
                     _VetoModel(), post_fn=bad_post,
                     prune=True,
                     retire_fn=lambda u, p: calls.append((u, p)))
    assert res["verdicts"]["hypothesis:h1"].get("transient") is True
    assert res["pruned"] == 0 and calls == []


def test_select_vetoed_unions_state_and_challenges():
    vetoed = select_vetoed(
        _els(), {"hypothesis:h1": {"verdict": "veto"},
                 "hypothesis:h2": {"verdict": "keep"},
                 "hypothesis:gone": {"verdict": "veto"}})
    assert vetoed == ["hypothesis:h1", "hypothesis:h3"]


def test_run_once_prune_removes_all_vetoed_no_reaudit(
        monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})

    def _no_model(*a, **k):
        raise AssertionError("prune must not resolve a model")

    monkeypatch.setattr(loop, "resolve_model", _no_model)
    state = str(tmp_path / "audit_state.json")
    save_state(state, {"hypothesis:h1": {"verdict": "veto"}})
    calls = []

    def retire(url, payload):
        calls.append((url, payload))
        return {"retired": [{"entity_id": hid, "removed": True,
                             "dropped_edges": 3}
                            for hid in payload["node_ids"]]}

    out = run_once("http://x", "auto", 0, False, state,
                   prune=True, retire_fn=retire)
    assert out["ok"] is True and out["mode"] == "prune"
    assert out["audited"] == 0 and out["llm_calls"] == 0
    assert out["vetoed"] == 2 and out["pruned"] == 2
    assert out["retired"] == ["hypothesis:h1", "hypothesis:h3"]
    assert calls == [("http://x/retire",
                      {"node_ids": ["hypothesis:h1",
                                    "hypothesis:h3"]})]


def test_run_once_prune_dry_run_lists_without_retire(
        monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    monkeypatch.setattr(
        loop, "resolve_model",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("prune must not resolve a model")))
    state = str(tmp_path / "audit_state.json")
    save_state(state, {"hypothesis:h1": {"verdict": "veto"}})
    calls = []
    out = run_once("http://x", "auto", 0, True, state,
                   prune=True,
                   retire_fn=lambda u, p: calls.append((u, p)))
    assert out["ok"] is True and out["pruned"] == 0
    assert out["vetoed"] == 2
    assert out["vetoed_ids"] == ["hypothesis:h1", "hypothesis:h3"]
    assert calls == []


def test_run_once_prune_empty_retires_nothing(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    els = [e for e in _els()
           if not (isinstance(e.get("data", {}).get("source"), str)
                   and e["data"].get("label") == "challenges")]
    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": els})
    monkeypatch.setattr(
        loop, "resolve_model",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("prune must not resolve a model")))
    state = str(tmp_path / "audit_state.json")
    save_state(state, {})
    calls = []
    out = run_once("http://x", "auto", 0, False, state,
                   prune=True,
                   retire_fn=lambda u, p: calls.append((u, p)))
    assert out["ok"] is True and out["pruned"] == 0
    assert out["vetoed"] == 0 and calls == []


def test_run_once_retires_fresh_veto(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    monkeypatch.setattr(loop, "resolve_model", lambda *a, **k: _VetoModel())
    state = str(tmp_path / "audit_state.json")
    retired = []

    def retire(url, payload):
        retired.append((url, payload))
        return {"retired": [{"entity_id": hid, "removed": True,
                             "dropped_edges": 3}
                            for hid in payload["node_ids"]]}

    out = run_once("http://x", "auto", 1, False, state,
                   post_fn=_ok_post, retire_fn=retire)
    assert out["ok"] is True
    assert out["vetoed"] == 1 and out["pruned"] == 1
    assert retired == [("http://x/retire",
                        {"node_ids": ["hypothesis:h1"]})]
    saved = load_state(state)
    assert saved["hypothesis:h1"]["verdict"] == "veto"
    assert saved["hypothesis:h1"]["pruned"] is True


def test_run_once_no_retire_on_veto_opt_out(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    monkeypatch.setattr(loop, "resolve_model", lambda *a, **k: _VetoModel())
    state = str(tmp_path / "audit_state.json")
    calls = []
    out = run_once("http://x", "auto", 1, False, state,
                   post_fn=_ok_post, retire_on_veto=False,
                   retire_fn=lambda u, p: calls.append((u, p)))
    assert out["ok"] is True
    assert out["vetoed"] == 1 and out["pruned"] == 0
    assert calls == []


def test_select_force_reaudits_challenged_and_known():
    cands = select_candidates(
        _els(), {"hypothesis:h1": {"verdict": "keep"},
                 "hypothesis:h2": {"verdict": "keep"}}, 0, force=True)
    assert [c["hid"] for c in cands] == ["hypothesis:h1",
                                         "hypothesis:h2",
                                         "hypothesis:h3"]


def test_run_once_force_reaudits_everything(monkeypatch, tmp_path):
    import scripts.audit_loop as loop

    monkeypatch.setattr(
        loop, "_get_json", lambda url, timeout=30: {"elements": _els()})
    state = str(tmp_path / "audit_state.json")
    save_state(state, {"hypothesis:h1": {"verdict": "keep"},
                       "hypothesis:h2": {"verdict": "keep"}})
    out = run_once("http://x", None, 0, False, state, force=True)
    assert out["ok"] is True
    assert (out["audited"], out["kept"], out["vetoed"]) == (3, 3, 0)
    assert set(load_state(state)) == {"hypothesis:h1",
                                      "hypothesis:h2",
                                      "hypothesis:h3"}


def test_select_candidates_carries_hypothesis_type():
    els = [
        N("kalshi:A", "Event", "A"),
        N("kalshi:B", "Event", "B"),
        N("hypothesis:typed", "Hypothesis", "A x B",
          {"type": "co_location", "mode": "co-location"}),
        N("hypothesis:legacy", "Hypothesis", "A x B",
          {"mode": "shared-subject"}),
        N("hypothesis:bare", "Hypothesis", "A x B"),
        E("hypothesis:typed", "kalshi:A", "supports"),
        E("hypothesis:typed", "kalshi:B", "supports"),
        E("hypothesis:legacy", "kalshi:A", "supports"),
        E("hypothesis:legacy", "kalshi:B", "supports"),
        E("hypothesis:bare", "kalshi:A", "supports"),
        E("hypothesis:bare", "kalshi:B", "supports"),
    ]
    by_hid = {c["hid"]: c for c in select_candidates(els, {}, 0)}
    assert by_hid["hypothesis:typed"]["htype"] == "co_location"
    assert by_hid["hypothesis:legacy"]["htype"] == "shared_context"
    assert by_hid["hypothesis:bare"]["htype"] is None


def test_audit_pass_forwards_claimed_type_to_model():
    seen = []

    class TypeSpy:
        def invoke(self, msgs):
            seen.extend(msgs)
            return SimpleNamespace(content="NO\nno shared place\n0.7")

    cands = [{"hid": "hypothesis:t", "title": "A x B",
              "events": [{"ticker": "A", "title": "A"},
                         {"ticker": "B", "title": "B"}],
              "articles": ["ev one"], "htype": "co_location"}]
    res = audit_pass("http://x", cands, TypeSpy(), post_fn=_ok_post)
    assert res["vetoed"] == 1
    text = " ".join(str(getattr(m, "content", "")) for m in seen)
    assert "co_location" in text
