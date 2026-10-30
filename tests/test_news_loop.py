"""Step 4 gates: mode rotation, pair detection, offline cycle. No network."""

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scripts.news_loop as nl
from graph import GraphStore, GraphUpdateAPI, commit_batch


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


def test_cycle_needs_two_topics(monkeypatch):
    assert nl.run_cycle("http://127.0.0.1:1", "http://127.0.0.1:1",
                        model=None)["ok"] is False
