"""Independent audit loop: selection, veto posts, state. No network."""

import json
from types import SimpleNamespace

from scripts.audit_loop import (audit_pass, load_state, run_once, save_state,
                                select_candidates)


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
