"""Step 1+2 gates: append-only log/snapshot + graph API service.

No network beyond localhost; no live sources. Concurrency test uses
threads against the real service object (mirrors N writers posting
overlapping batches) plus one ephemeral-port HTTP roundtrip.
"""

import json
import threading

from fastapi.testclient import TestClient

from graph import (
    GraphStore,
    GraphUpdateAPI,
    boot,
    commit_batch,
    replay_log,
)
from graph.persist import append_finding
from services.graph_server import GraphService, create_app


def _node_prop(agent, nid, kind="Event", title=None, conf=0.9):
    return {"agent": agent, "action": "add-node", "entity_id": nid,
            "detail": json.dumps({"kind": kind, "title": title or nid}),
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

    chunks = [proposals[i::4] for i in range(4)]
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
