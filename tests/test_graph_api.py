"""Step 1+2 gates: append-only log/snapshot + graph API service.

No network beyond localhost; no live sources. Concurrency test uses
threads against the real service object (mirrors N writers posting
overlapping batches) plus one ephemeral-port HTTP roundtrip.
"""

import json
import threading

from fastapi.testclient import TestClient

from graph import (
    Edge,
    GraphStore,
    GraphUpdateAPI,
    Node,
    boot,
    commit_batch,
    materialize_derived_links,
    replay_log,
)
from graph.persist import append_finding
from services.graph_api import GraphService, create_app


def _node_prop(agent, nid, kind="Event", title=None, conf=0.9, props=None):
    detail = {"kind": kind, "title": title or nid}
    if props is not None:
        detail["props"] = props
    return {"agent": agent, "action": "add-node", "entity_id": nid,
            "detail": json.dumps(detail),
            "confidence": conf}


def _edge_prop(agent, src, dst, rel="relates-to", conf=0.9):
    return {"agent": agent, "action": "add-edge",
            "entity_id": f"{src}->{dst}",
            "detail": json.dumps({"src": src, "dst": dst, "rel": rel}),
            "confidence": conf}


def test_commit_batch_accept_reject_and_lanes(tmp_path):
    api = GraphUpdateAPI(GraphStore())
    log = str(tmp_path / "g.log")
    snap = str(tmp_path / "g.json")
    out = commit_batch(api, [
        _node_prop("scout", "kalshi:A"),
        # wrong lane: scout may not add-edge
        _edge_prop("scout", "kalshi:A", "kalshi:A"),
        # skeptic veto: zero confidence
        _node_prop("investigator", "kalshi:VETO", conf=0.0),
        # invalid kind
        _node_prop("scout", "x:1", kind="Nope"),
    ], log_path=log, snapshot_path=snap)
    assert out["revision"] == 1
    reasons = [v["reason"] for v in out["verdicts"]]
    assert reasons[0] == "created"
    assert "may not" in reasons[1]  # wrong-lane refusal
    assert "vetoed" in reasons[2]
    assert "invalid kind" in reasons[3]
    assert set(api.store.nodes) == {"kalshi:A"}
    # log + snapshot both written
    rec = json.loads(open(log).read().strip())
    assert rec["revision"] == 1 and len(rec["verdicts"]) == 4
    back = GraphUpdateAPI.load(snap)
    assert back.revision == 1 and set(back.store.nodes) == {"kalshi:A"}


def test_odds_history_survives_in_log(tmp_path):
    """Live odds mutate the node; the log keeps every point for time travel."""
    log = str(tmp_path / "g.log")
    snap = str(tmp_path / "g.json")
    api = GraphUpdateAPI(GraphStore())
    for price in (55, 63):
        out = commit_batch(api, [{
            "agent": "scout", "action": "add-node", "entity_id": "kalshi:E",
            "detail": json.dumps({
                "kind": "Event", "title": "E",
                "props": {"markets": {"T": {"price_cents": price}}}}),
            "confidence": 0.9}], log_path=log, snapshot_path=snap)
        assert out["revision"] in (1, 2)
    live = api.store.nodes["kalshi:E"].props["markets"]["T"]
    assert live["price_cents"] == 63  # live
    prices = [json.loads(line)["proposals"][0]["detail"]
              for line in open(log) if line.strip()]
    assert [json.loads(d)["props"]["markets"]["T"]["price_cents"]
            for d in prices] == [55, 63]
    replayed = GraphUpdateAPI(GraphStore())
    replay_log(replayed, log, after_revision=0)
    assert replayed.store.nodes["kalshi:E"].props["markets"]["T"][
        "price_cents"] == 63


def test_commit_batch_runs_connector_derive_without_entity(tmp_path):
    """derive-links has no entity_id; the weak-proposal gate must allow it."""
    api = GraphUpdateAPI(GraphStore())
    for nid, kind in (("kalshi:A", "Event"), ("kalshi:B", "Event"),
                      ("article:x", "Article"), ("subject:s", "Subject")):
        api.store.add_node(Node(nid, kind, nid))
    for src, dst, rel in (("article:x", "subject:s", "mentions"),
                          ("article:x", "kalshi:A", "about"),
                          ("article:x", "kalshi:B", "about")):
        api.store.add_edge(Edge(src, dst, rel, "t"))
    out = commit_batch(api, [{
        "agent": "connector", "action": "derive-links",
        "entity_id": "", "detail": "", "confidence": 0.9}],
        log_path=str(tmp_path / "g.log"))
    verdict = out["verdicts"][0]
    assert verdict["committed"] and "derived" in verdict["reason"]
    # Nothing derived is persisted; the read path still sees the mesh.
    assert not any(e.rel == "shares-subject-with" for e in api.store.edges)
    links = [e for e in materialize_derived_links(api.store)
             if e.rel == "shares-subject-with"]
    assert {(e.src, e.dst) for e in links} == {("kalshi:A", "kalshi:B")}


def test_graph_payload_derives_links_without_storing_them(tmp_path):
    svc = GraphService(snapshot_path=str(tmp_path / "s.json"),
                       log_path=str(tmp_path / "g.log"),
                       findings_path=str(tmp_path / "f.jsonl"))
    proposals = [
        _node_prop("scout", "kalshi:A"), _node_prop("scout", "kalshi:B"),
        _node_prop("scout", "article:x", kind="Article",
                   props={"subjects": ["spacex"]}),
        _edge_prop("news-agent", "article:x", "kalshi:A", rel="about"),
        _edge_prop("news-agent", "article:x", "kalshi:B", rel="about"),
    ]
    svc.submit(proposals)
    payload = svc.graph_payload()
    assert payload["nodes_count"] == 3  # persisted only
    assert payload["derived_nodes_count"] == 0  # opt-in via expand
    assert payload["edges_count"] == 2  # primitive only
    assert payload["derived_count"] == 0
    # Opt-in restores the read-time set without persisting it.
    full = svc.graph_payload(expand="all")
    assert full["derived_nodes_count"] == 1  # folded Subject
    assert full["derived_count"] == 2  # folded mention + one mesh
    labels = [el["data"]["label"] for el in full["elements"]
              if "source" in el["data"]]
    assert labels.count("shares-subject-with") == 1
    assert labels.count("mentions") == 1
    node_ids = [el["data"]["id"] for el in full["elements"]
                if "source" not in el["data"]]
    assert "subject:spacex" in node_ids
    assert not any(e.rel in ("shares-subject-with", "mentions")
                   for e in svc.api.store.edges)


def test_happening_kind_commits(tmp_path):
    api = GraphUpdateAPI(GraphStore())
    out = commit_batch(api, [
        _node_prop("scout", "happening:fed-hike", kind="Happening",
                   title="Fed hikes"),
    ], log_path=str(tmp_path / "g.log"))
    assert out["verdicts"][0]["committed"]
    assert api.store.nodes["happening:fed-hike"].kind == "Happening"


def test_boot_replays_log_after_snapshot(tmp_path):
    log = str(tmp_path / "g.log")
    snap = str(tmp_path / "g.json")
    api = GraphUpdateAPI(GraphStore())
    commit_batch(api, [_node_prop("scout", "kalshi:A")],
                 log_path=log, snapshot_path=snap)
    commit_batch(api, [_node_prop("scout", "kalshi:B")],
                 log_path=log, snapshot_path=snap)
    assert api.revision == 2
    # wipe snapshot back to rev 1, replay must restore B
    first = json.loads(open(log).readline())
    api2 = GraphUpdateAPI(GraphStore())
    for p, v in zip(first["proposals"], first["verdicts"]):
        assert v["committed"]
    from graph import proposals_to_commit
    proposals_to_commit(api2, "scout", "add-node", "kalshi:A",
                        json.dumps({"kind": "Event", "title": "kalshi:A"}))
    api2.revision = 1
    api2.save(snap)
    top = replay_log(api2, log, after_revision=api2.revision)
    assert top == 2
    assert set(api2.store.nodes) == {"kalshi:A", "kalshi:B"}
    assert boot(snap, log).revision == 2


def test_legacy_plain_snapshot_loads(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"nodes": [{"id": "kalshi:X", "kind": "Event",
                                        "title": "X", "created_at": "t"}],
                             "edges": []}))
    back = GraphUpdateAPI.load(str(p))
    assert back.revision == 0
    assert set(back.store.nodes) == {"kalshi:X"}


def test_findings_append(tmp_path):
    fp = str(tmp_path / "findings.jsonl")
    rec = append_finding(fp, {"term": "spacex", "urls": ["http://x"]})
    assert rec["finding"]["term"] == "spacex" and rec["recorded_at"]
    assert json.loads(open(fp).readline())["finding"]["urls"] == ["http://x"]


def test_as_of_time_travel_via_service(tmp_path):
    svc = GraphService(snapshot_path=str(tmp_path / "s.json"),
                       log_path=str(tmp_path / "g.log"),
                       findings_path=str(tmp_path / "f.jsonl"))
    svc.submit([dict(_node_prop("scout", "article:old"),
                     detail=json.dumps({"kind": "Article", "title": "old",
                                        "valid_at": "20261028T010000Z"}))])
    svc.submit([dict(_node_prop("scout", "article:new"),
                     detail=json.dumps({"kind": "Article", "title": "new",
                                        "valid_at": "20261029T010000Z"}))])
    full = svc.graph_payload()
    assert full["nodes_count"] == 2
    past = svc.graph_payload(at="20261028T120000Z")
    assert past["nodes_count"] == 1
    assert full["revision"] == 2


def test_concurrent_overlapping_batches_equal_sequential(tmp_path):
    """20 agents, overlapping writes: final state equals sequential."""
    proposals = [_node_prop("scout", f"kalshi:N{i}") for i in range(20)]
    proposals += [_node_prop("scout", "kalshi:SHARED") for _ in range(8)]

    def run_sequential():
        api = GraphUpdateAPI(GraphStore())
        for p in proposals:
            commit_batch(api, [p])
        return set(api.store.nodes), api.revision

    want_nodes, want_rev = run_sequential()

    svc = GraphService(snapshot_path=str(tmp_path / "s.json"),
                       log_path=str(tmp_path / "g.log"),
                       findings_path=str(tmp_path / "f.jsonl"))
    errors: list = []

    def worker(chunk):
        try:
            for p in chunk:
                svc.submit([p])
                # snapshot must stay loadable after every batch
                GraphUpdateAPI.load(svc.snapshot_path)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    chunks = [proposals[i::20] for i in range(20)]
    threads = [threading.Thread(target=worker, args=(c,)) for c in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert set(svc.api.store.nodes) == want_nodes
    assert svc.api.revision == want_rev == len(proposals)
    # log has one record per batch, revisions strictly increasing
    revs = [json.loads(line)["revision"]
            for line in open(svc.log_path) if line.strip()]
    assert revs == list(range(1, len(proposals) + 1))


def test_http_roundtrip_health_proposals_graph(tmp_path):
    svc = GraphService(snapshot_path=str(tmp_path / "s.json"),
                       log_path=str(tmp_path / "g.log"),
                       findings_path=str(tmp_path / "f.jsonl"))
    client = TestClient(create_app(service=svc))
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["revision"] == 0
    r = client.post("/proposals", json={
        "proposals": [
            _node_prop("scout", "kalshi:A"),
            _edge_prop("scout", "kalshi:A", "kalshi:A"),  # refused
        ]})
    assert r.status_code == 200
    resp = r.json()
    assert resp["revision"] == 1
    assert resp["verdicts"][0]["committed"]
    assert "may not" in resp["verdicts"][1]["reason"]
    r = client.get("/graph")
    assert r.status_code == 200
    assert r.json()["revision"] == 1
    assert r.headers.get("etag") == "1"
    r = client.get("/graph", headers={"If-None-Match": "1"})
    assert r.status_code == 304
    r = client.post("/findings", json={"finding": {"term": "x"}})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/nope").status_code == 404


def test_proposals_validation_and_defaults(tmp_path):
    svc = GraphService(snapshot_path=str(tmp_path / "s.json"),
                       log_path=str(tmp_path / "g.log"),
                       findings_path=str(tmp_path / "f.jsonl"))
    client = TestClient(create_app(service=svc))
    # default agent fallback from the envelope
    r = client.post("/proposals", json={
        "agent": "scout",
        "proposals": [{"action": "add-node", "entity_id": "kalshi:D",
                       "detail": json.dumps({"kind": "Event",
                                             "title": "D"})}]})
    assert r.status_code == 200
    assert r.json()["verdicts"][0]["committed"]
    # malformed shape rejected by pydantic, not a 500
    r = client.post("/proposals", json={"proposals": "nope"})
    assert r.status_code == 422


def test_retire_endpoint_removes_node_and_edges(tmp_path):
    svc = GraphService(snapshot_path=str(tmp_path / "s.json"),
                       log_path=str(tmp_path / "g.log"),
                       findings_path=str(tmp_path / "f.jsonl"))
    client = TestClient(create_app(service=svc))
    client.post("/proposals", json={
        "proposals": [
            _node_prop("news-agent", "kalshi:A"),
            _node_prop("news-agent", "hypothesis:h1",
                       kind="Hypothesis"),
            _edge_prop("news-agent", "hypothesis:h1", "kalshi:A",
                       rel="asserts"),
        ]})
    r = client.post("/retire", json={"node_ids": ["hypothesis:h1"]})
    assert r.status_code == 200
    body = r.json()
    assert body["retired"][0]["removed"] is True
    r = client.get("/graph")
    ids = [el["data"].get("id") for el in r.json()["elements"]]
    assert "hypothesis:h1" not in ids
