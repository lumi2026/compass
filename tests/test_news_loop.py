"""Step 4 gates: mode rotation, pair detection, offline cycle. No network."""

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scripts.news_loop as nl
from graph import GraphStore, GraphUpdateAPI, Node, commit_batch


def _rows():
    shared = "http://loop.test/shared"
    return [
        {"ngram": "alpha", "type": 1, "lang": "en",
         "pre": "x", "post": "beta aero lifts", "url": shared},
        {"ngram": "gamma", "type": 1, "lang": "en",
         "pre": "y", "post": "delta games open", "url": shared},
        {"ngram": "alpha", "type": 1, "lang": "en",
         "pre": "x", "post": "beta solo flight", "url": "http://loop.test/a1"},
        {"ngram": "gamma", "type": 1, "lang": "en",
         "pre": "y", "post": "delta solo match", "url": "http://loop.test/b1"},
    ]


def _snap(path):
    with gzip.open(str(path), "wt") as f:
        for r in _rows():
            f.write(json.dumps(r) + "\n")


def _topics():
    return [{"event_ticker": "EA", "title": "Alpha Beta Aero",
             "terms": ["alpha beta"]},
            {"event_ticker": "EB", "title": "Gamma Delta Games",
             "terms": ["gamma delta"]}]


def test_next_mode_rotates_and_pins():
    assert [nl.next_mode(c) for c in range(6)] == list(nl.MODES) + [nl.MODES[0]]
    assert nl.next_mode(99, pinned="burst") == "burst"


def test_co_mention_pair_on_fixture(tmp_path):
    gz = tmp_path / "s.json.gz"
    _snap(gz)
    from sources.ngrams import ngram_mentions

    m = ngram_mentions(["alpha beta", "gamma delta"], str(gz),
                       per_term_cap=30)
    pairs = nl.shared_url_pairs(_topics(), m)
    assert len(pairs) == 1
    assert (pairs[0]["a"], pairs[0]["b"]) == ("EA", "EB")
    assert pairs[0]["urls"] == ["http://loop.test/shared"]


def test_shared_subject_pair_on_fixture(tmp_path):
    gz = tmp_path / "s.json.gz"
    _snap(gz)
    from sources.ngrams import ngram_mentions

    m = ngram_mentions(["alpha beta", "gamma delta"], str(gz),
                       per_term_cap=30)
    topics = _topics() + [{"event_ticker": "EC", "title": "Alpha Beta Club",
                           "terms": ["alpha beta"]}]
    pairs = nl.shared_subject_pairs(topics, m)
    assert {(p["a"], p["b"]) for p in pairs} == {("EA", "EC")}
    assert pairs[0]["subject"] == "alpha beta"


def test_burst_topics_spike_only():
    topics = _topics()
    new_m = {"alpha beta": {"count": 6, "urls": ["u1"]},
             "gamma delta": {"count": 1, "urls": []}}
    old_m = {"alpha beta": {"count": 1, "urls": []},
             "gamma delta": {"count": 1, "urls": []}}
    bursting = nl.burst_topics(topics, new_m, old_m)
    assert [b["event_ticker"] for b in bursting] == ["EA"]


def test_entity_overlap_pairs():
    topics = _topics()
    kept = [
        {"url": "u1", "title": "Mayor Smith visits Paris summit",
         "topics": ["EA"]},
        {"url": "u2", "title": "Smith opens Paris office today",
         "topics": ["EB"]},
        {"url": "u3", "title": "Local team wins friendly match",
         "topics": ["EB"]},
    ]
    pairs = nl.entity_overlap_pairs(topics, kept)
    assert len(pairs) == 1
    assert (pairs[0]["a"], pairs[0]["b"]) == ("EA", "EB")
    assert pairs[0]["urls"]


def test_pearson_and_closes():
    assert nl.pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == 1.0
    assert nl.pearson([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None
    assert nl.pearson([1.0], [1.0]) is None
    assert nl.market_closes([{"close": 55}, {"close": None},
                             {"close": "bad"}, {"close": 57}]) == [55.0, 57.0]


def test_hypothesis_title_fallback():
    title, conf = nl.hypothesis_title("EA", "EB", ["u1", "u2"],
                                      [type("A", (), {"title": "T1"})()],
                                      model=None)
    assert "2 articles" in title and conf == 0.6


class _Stub:
    def __init__(self, api):
        self.api = api
        self.findings = []


def _serve_graph(api):
    holder = _Stub(api)

    class Handler(BaseHTTPRequestHandler):
        def _ok(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            els = [{"data": {"id": n.id, "label": n.title,
                             "kind": n.kind}}
                   for n in holder.api.store.nodes.values()]
            self._ok({"elements": els})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/proposals":
                out = commit_batch(
                    holder.api, payload.get("proposals", []),
                    log_path=None, snapshot_path=None)
                self._ok({"revision": out["revision"],
                          "verdicts": out["verdicts"],
                          "derived_fresh": out["derived_fresh"]})
            elif self.path == "/findings":
                holder.findings.append(payload.get("finding", {}))
                self._ok({"ok": True})
            else:
                self._ok({})

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t, holder


def _serve_kalshi():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"events": [
                {"event_ticker": "EA", "title": "Alpha Beta Aero"},
                {"event_ticker": "EB", "title": "Gamma Delta Games"},
            ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


def test_offline_cycle_posts_hypothesis(tmp_path, monkeypatch):
    gz = tmp_path / "s.json.gz"
    _snap(gz)
    monkeypatch.setattr(nl, "_snapshot_pair",
                        lambda: ("20261030T000000Z", str(gz),
                                 "20261029T000000Z", str(gz)))
    api = GraphUpdateAPI(GraphStore())
    gsrv, gt, holder = _serve_graph(api)
    ksrv, kt = _serve_kalshi()
    try:
        summary = nl.run_cycle(
            f"http://127.0.0.1:{gsrv.server_address[1]}",
            f"http://127.0.0.1:{ksrv.server_address[1]}",
            cycle=0, model=None)
    finally:
        gsrv.shutdown()
        ksrv.shutdown()
        gt.join(timeout=10)
        kt.join(timeout=10)
    assert summary["ok"] and summary["mode"] == "co-mention"
    assert summary["pairs"] == 1 and summary["proposals"] > 0
    assert summary["llm_calls"] == 0  # model=None: extractive fallback
    assert "hypothesis:loop-ea-eb" in api.store.nodes
    from graph import article_id

    assert article_id("http://loop.test/shared") in api.store.nodes
    assert holder.findings and holder.findings[0]["pairs"] == 1


def test_snapshot_pair_skips_unlanded_marks(tmp_path, monkeypatch):
    import urllib.error

    marks = ["20261030210100", "20261030204600", "20261030200100"]
    monkeypatch.setattr(nl, "snapshot_candidates", lambda *a, **k: marks)

    import sources.ngrams as ng

    def fake_download(ts, dest):
        if ts == marks[0]:
            raise urllib.error.HTTPError(ts, 404, "miss", {}, None)
        with open(dest, "wb") as f:
            f.write(b"")
        return dest

    monkeypatch.setattr(ng, "download_snapshot", fake_download)
    new_ts, new_path, old_ts, old_path = nl._snapshot_pair(str(tmp_path))
    assert new_ts == marks[1]  # 21:01 404 skipped
    assert old_ts == marks[2] and new_path.endswith(".json.gz")
    assert old_path.endswith(".json.gz")


def test_odds_cycle_correlated_pair_posts(monkeypatch):
    gsrv, gt, holder = _serve_graph(GraphUpdateAPI(GraphStore()))

    def fake_markets(url, event):
        return [{"ticker": f"{event}-M1", "volume_24h": 100}]

    def fake_history(url, ticker):
        base = [50.0, 52.0, 55.0, 58.0]
        return [{"close": c if ticker.endswith("EA-M1") else c * 2}
                for c in base]

    monkeypatch.setattr(nl, "_kalshi_markets", fake_markets)
    monkeypatch.setattr(nl, "_kalshi_history", fake_history)
    try:
        summary = nl._odds_cycle(
            f"http://127.0.0.1:{gsrv.server_address[1]}",
            "http://127.0.0.1:9", _topics(), 10, None)
    finally:
        gsrv.shutdown()
        gt.join(timeout=10)
    assert summary["ok"] and summary["pairs"] == 1
    assert summary["mode"] == "odds-correlation"
    assert summary["llm_calls"] == 0
    assert "hypothesis:odds-ea-eb" in holder.api.store.nodes


def test_followups_fan_out_on_new_links(tmp_path, monkeypatch):
    from graph import Node

    gz = tmp_path / "s.json.gz"
    _snap(gz)
    monkeypatch.setattr(nl, "_snapshot_pair",
                        lambda *a, **k: ("20261030T000000Z", str(gz),
                                         "20261029T000000Z", str(gz)))
    api = GraphUpdateAPI(GraphStore())
    for eid in ("kalshi:EA", "kalshi:EB"):
        api.commit(Node(id=eid, kind="Event", title=eid))
    gsrv, gt, holder = _serve_graph(api)
    ksrv, kt = _serve_kalshi()
    try:
        summary = nl.run_cycle(
            f"http://127.0.0.1:{gsrv.server_address[1]}",
            f"http://127.0.0.1:{ksrv.server_address[1]}",
            cycle=0, model=None)
    finally:
        gsrv.shutdown()
        ksrv.shutdown()
        gt.join(timeout=10)
        kt.join(timeout=10)
    assert summary["ok"] and summary["followups"] == 2
    followup_posts = [f for f in holder.findings if f.get("followups")]
    assert followup_posts and len(followup_posts[0]["followups"]) == 2
    links = [e for e in api.store.edges if e.rel == "shares-subject-with"]
    assert {(e.src, e.dst) for e in links} == {
        ("kalshi:EA", "kalshi:EB"), ("kalshi:EB", "kalshi:EA")}


def test_scan_history_merges_stride_snapshots(tmp_path, monkeypatch):
    import gzip as _gzip

    def _write(path, rows):
        with _gzip.open(str(path), "wt") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    gz1 = tmp_path / "n1.json.gz"
    gz2 = tmp_path / "n2.json.gz"
    _write(gz1, [{"ngram": "alpha", "type": 1, "lang": "en",
                  "pre": "x", "post": "beta one",
                  "url": "http://loop.test/shared"},
                 {"ngram": "alpha", "type": 1, "lang": "en",
                  "pre": "x", "post": "beta solo",
                  "url": "http://loop.test/only1"}])
    _write(gz2, [{"ngram": "alpha", "type": 1, "lang": "en",
                  "pre": "x", "post": "beta two",
                  "url": "http://loop.test/shared"},
                 {"ngram": "alpha", "type": 1, "lang": "en",
                  "pre": "x", "post": "beta other",
                  "url": "http://loop.test/only2"}])
    monkeypatch.setattr(nl, "_snapshot_stride_list",
                        lambda *a, **k: [("t1", str(gz1)), ("t2", str(gz2))])
    merged, per = nl.scan_history(["alpha beta"], hours_back=48,
                                  stride_hours=6)
    assert merged["alpha beta"]["count"] == 4
    assert merged["alpha beta"]["urls"] == ["http://loop.test/only1",
                                            "http://loop.test/only2",
                                            "http://loop.test/shared"]
    assert [ts for ts, _ in per] == ["t1", "t2"]


def test_stride_list_spacing_and_bound(tmp_path, monkeypatch):
    import datetime

    base = datetime.datetime(2026, 10, 30, 12, 0)
    marks = [(base - datetime.timedelta(minutes=15 * i)).strftime(
        "%Y%m%d%H%M%S") for i in range(53)]  # 13h of marks
    monkeypatch.setattr(nl, "snapshot_candidates", lambda *a, **k: marks)

    import sources.ngrams as ng

    def fake_download(ts, dest):
        with open(dest, "wb") as f:
            f.write(b"")
        return dest

    monkeypatch.setattr(ng, "download_snapshot", fake_download)
    out = nl._snapshot_stride_list(hours_back=13, stride_hours=6,
                                   dest_dir=str(tmp_path), max_snaps=12)
    assert [ts for ts, _ in out] == [marks[0], marks[24], marks[48]]
    out = nl._snapshot_stride_list(hours_back=13, stride_hours=6,
                                   dest_dir=str(tmp_path), max_snaps=2)
    assert len(out) == 2


def test_auditor_fallback_keeps_and_parses():
    from squad.auditor import audit_pair

    kept = audit_pair("EA", "t", "EB", "t", ["x"], "co-mention", None)
    assert kept.keep and kept.confidence == 0.5

    class FakeMsg:
        content = "NO\nMexico article, US markets\n0.2"

    class FakeModel:
        def invoke(self, messages):
            assert len(messages) == 2
            return FakeMsg()

    veto = audit_pair("EA", "t", "EB", "t", ["x"], "co-mention",
                      FakeModel())
    assert veto.keep is False and veto.reason.startswith("Mexico")
    assert veto.confidence == 0.2

    class YesModel:
        def invoke(self, messages):
            return type("R", (), {
                "content": "YES\nsame race\n0.9"})()

    yes = audit_pair("EA", "t", "EB", "t", ["x"], "co-mention",
                     YesModel())
    assert yes.keep and yes.confidence == 0.9

    class BrokenModel:
        def invoke(self, messages):
            raise RuntimeError("down")

    broken = audit_pair("EA", "t", "EB", "t", ["x"], "co-mention",
                        BrokenModel())
    assert broken.keep  # fail-open


def test_auditor_lane_commits_challenges():
    from graph import proposals_to_commit

    api = GraphUpdateAPI(GraphStore())
    hid = "hypothesis:loop-ea-eb"
    api.commit(Node(id=hid, kind="Hypothesis", title="H"))
    good = proposals_to_commit(
        api, "auditor", "add-node", f"audit:{hid}",
        json.dumps({"kind": "Audit", "title": "audit flags",
                     "props": {"target": hid, "verdict": "reject"}}))
    assert good.committed
    edge = proposals_to_commit(
        api, "auditor", "add-edge", f"audit:{hid}",
        json.dumps({"src": f"audit:{hid}", "dst": hid,
                    "rel": "challenges"}))
    assert edge.committed
    assert api.store.nodes[f"audit:{hid}"].kind == "Audit"


def test_cycle_veto_skips_pair_and_challenges_existing(tmp_path,
                                                        monkeypatch):
    from squad.auditor import AuditResult

    gz = tmp_path / "s.json.gz"
    _snap(gz)
    monkeypatch.setattr(nl, "_snapshot_pair",
                        lambda *a, **k: ("20261030T000000Z", str(gz),
                                         "20261029T000000Z", str(gz)))
    monkeypatch.setattr(
        nl, "audit_pair",
        lambda *a, **k: AuditResult(False, "Mexico article, US markets",
                                    0.2))
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="hypothesis:loop-ea-eb", kind="Hypothesis",
                    title="old bad link"))
    gsrv, gt, holder = _serve_graph(api)
    ksrv, kt = _serve_kalshi()
    try:
        summary = nl.run_cycle(
            f"http://127.0.0.1:{gsrv.server_address[1]}",
            f"http://127.0.0.1:{ksrv.server_address[1]}",
            cycle=0, model=None)
    finally:
        gsrv.shutdown()
        ksrv.shutdown()
        gt.join(timeout=10)
        kt.join(timeout=10)
    assert summary["ok"] and summary["pairs"] == 0
    assert summary["vetoed"] == 1 and summary["llm_calls"] == 0
    assert "audit:hypothesis:loop-ea-eb" in api.store.nodes
    flagged = [e for e in api.store.edges if e.rel == "challenges"]
    assert [(e.src, e.dst) for e in flagged] == [
        ("audit:hypothesis:loop-ea-eb", "hypothesis:loop-ea-eb")]
    assert holder.findings and holder.findings[0]["vetoed"] == 1


INEGI_EVIDENCE = ["INEGI: Mexico Q3 GDP growth 0.9% quarterly"]
WSJ_EVIDENCE = ["WSJ: U.S. GDP growth hit 3.4%"]
BACKER_EVIDENCE = ["Musk backs biotech entrepreneur in funding round"]
MODI_EVIDENCE = ["Modi Canada trade visit; PM Narendra Modi expected"]

#: Labeled from the 11 live pairs, 2026-10-30/31. Ground truth is
#: market identity, not story similarity: KXGDPYEAR tracks US GDP,
#: so INEGI (Mexico) evidence is spurious even when the story rhymes.
PAIR_FIXTURES = [
    ("KXGDPYEAR-36", "US GDP 2036 annual growth",
     "KXNOMGDPGROWTH-37JAN28", "US nominal GDP growth Jan 2037",
     WSJ_EVIDENCE, True),
    ("KXGDPYEAR-34", "US GDP 2034 annual growth",
     "KXGDPYEAR-35", "US GDP 2035 annual growth", INEGI_EVIDENCE, False),
    ("KXGDPYEAR-34", "US GDP 2034 annual growth",
     "KXGDPYEAR-36", "US GDP 2036 annual growth", INEGI_EVIDENCE, False),
    ("KXGDPYEAR-34", "US GDP 2034 annual growth",
     "KXNOMGDPGROWTH-35JAN28", "US nominal GDP growth Jan 2035",
     INEGI_EVIDENCE, False),
    ("KXGDPYEAR-34", "US GDP 2034 annual growth",
     "KXNOMGDPGROWTH-36JAN28", "US nominal GDP growth Jan 2036",
     INEGI_EVIDENCE, False),
    ("KXGDPYEAR-34", "US GDP 2034 annual growth",
     "KXNOMGDPGROWTH-37JAN28", "US nominal GDP growth Jan 2037",
     INEGI_EVIDENCE, False),
    ("KXGDPYEAR-35", "US GDP 2035 annual growth",
     "KXGDPYEAR-36", "US GDP 2036 annual growth", INEGI_EVIDENCE, False),
    ("KXGDPYEAR-35", "US GDP 2035 annual growth",
     "KXNOMGDPGROWTH-35JAN28", "US nominal GDP growth Jan 2035",
     INEGI_EVIDENCE, False),
    ("KXGDPYEAR-35", "US GDP 2035 annual growth",
     "KXNOMGDPGROWTH-36JAN28", "US nominal GDP growth Jan 2036",
     INEGI_EVIDENCE, False),
    ("KXELONMARS-99", "Will Elon Musk visit Mars in his lifetime?",
     "KXPERSONPRESMAM-45", "Mamdani presidential speculation",
     BACKER_EVIDENCE, False),
    ("KXNEXTISRAELPM-45JAN01", "Who will succeed Netanyahu as PM?",
     "KXNEXTUKPRIMEMIN-35JAN01", "Who will be the next UK PM?",
     MODI_EVIDENCE, False),
]


class ScriptModel:
    """Canned per-turn replies; records message lists for assertions."""

    def __init__(self, replies):
        self.replies = replies
        self.seen = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        r = self.replies[min(len(self.seen) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"content": r})()


def test_deliberate_keep_threads_history():
    from squad.auditor import deliberate_pair

    m = ScriptModel(["Link sentence here.",
                     "YES\nholds up\n0.8"])
    d = deliberate_pair("EA", "A market", "EB", "B market", ["ev one"],
                        "co-mention", m)
    assert d.keep and d.title == "Link sentence here." and d.turns_used == 2
    assert d.confidence == 0.8
    # turn 2 sees turn 1: 4 messages, the AI proposal quoted back
    assert len(m.seen[1]) == 4
    assert "Link sentence here." in str(m.seen[1][2].content)


def test_deliberate_veto_then_drop():
    from squad.auditor import deliberate_pair

    m = ScriptModel(["Bad link sentence.",
                     "NO\nmexico not us\n0.2",
                     "DROP\nnothing salvageable"])
    d = deliberate_pair("EA", "A", "EB", "B", ["mexico evidence"],
                        "co-mention", m)
    assert d.keep is False and d.turns_used == 3
    assert "salvageable" in d.reason


def test_deliberate_veto_then_salvage():
    from squad.auditor import deliberate_pair

    m = ScriptModel(["Bad link sentence.",
                     "NO\nwrong country\n0.3",
                     "Both track GDP releases\nnarrower"])
    d = deliberate_pair("EA", "A", "EB", "B", ["gdp release"],
                        "co-mention", m)
    assert d.keep and d.title == "Both track GDP releases"
    assert d.turns_used == 3


def test_deliberate_fallbacks():
    from squad.auditor import deliberate_pair

    d = deliberate_pair("EA", "A", "EB", "B", ["ev"], "co-mention", None)
    assert d.keep and d.turns_used == 0
    m = ScriptModel([RuntimeError("down")])
    d = deliberate_pair("EA", "A", "EB", "B", ["ev"], "co-mention", m)
    assert d.keep and d.turns_used == 0  # failed before completing a turn


def test_cycle_deliberate_without_model_matches_singleshot(tmp_path,
                                                            monkeypatch):
    gz = tmp_path / "s.json.gz"
    _snap(gz)
    monkeypatch.setattr(nl, "_snapshot_pair",
                        lambda *a, **k: ("20261030T000000Z", str(gz),
                                         "20261029T000000Z", str(gz)))
    api = GraphUpdateAPI(GraphStore())
    gsrv, gt, holder = _serve_graph(api)
    ksrv, kt = _serve_kalshi()
    try:
        summary = nl.run_cycle(
            f"http://127.0.0.1:{gsrv.server_address[1]}",
            f"http://127.0.0.1:{ksrv.server_address[1]}",
            cycle=0, model=None, deliberate=True)
    finally:
        gsrv.shutdown()
        ksrv.shutdown()
        gt.join(timeout=10)
        kt.join(timeout=10)
    assert summary["ok"] and summary["pairs"] == 1
    assert summary["llm_calls"] == 0
    assert "hypothesis:loop-ea-eb" in api.store.nodes


def test_cycle_needs_two_topics(monkeypatch):
    assert nl.run_cycle("http://127.0.0.1:1", "http://127.0.0.1:1",
                        model=None)["ok"] is False
