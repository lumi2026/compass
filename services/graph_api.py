"""Graph API service on FastAPI + Pydantic (step 2 of the living loop).

One authoritative in-memory GraphUpdateAPI, every mutation serialized
through a lock, persisted atomically (temp + rename) plus an
append-only commit log after each batch.

  GET  /health              {ok, nodes, edges, revision}
  GET  /graph               elements + revision (+counts);
                             persisted nodes/edges only by default
  GET  /graph?expand=...   opt into read-time materialization:
                           mentions, markets, mesh, all (comma-separated)
  GET  /graph?at=<ts>       time travel (valid_at <= ts, "" always kept)
  POST /proposals           {proposals: [{agent, action, entity_id,
                             detail, confidence?}]} -> per-proposal verdicts
  POST /retire              {node_ids: [...]} -> remove nodes + edges
  POST /findings            {finding: {...}} raw ungated append

Polling: send If-None-Match: <revision>, get 304 when unchanged.
Non-goals: auth, multi-process store (one machine, one writer).
"""

import threading
from typing import Any

import click
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field

from graph import (
    GraphUpdateAPI, boot, commit_batch, materialize_derived_links,
    materialize_derived_nodes, subgraph_as_of, to_cytoscape,
)
from graph.persist import append_finding, commit_retire


class ProposalIn(BaseModel):
    agent: str = ""
    action: str = ""
    entity_id: str = ""
    detail: str = ""
    confidence: float = 0.5


class ProposalsIn(BaseModel):
    proposals: list[ProposalIn] = Field(default_factory=list)
    agent: str = ""  # default agent when an item omits it


class FindingIn(BaseModel):
    finding: dict[str, Any] = Field(default_factory=dict)


class RetireIn(BaseModel):
    node_ids: list[str] = Field(default_factory=list)


class RetiredOut(BaseModel):
    entity_id: str = ""
    removed: bool = False
    dropped_edges: int = 0


class RetireOut(BaseModel):
    revision: int
    retired: list[RetiredOut] = Field(default_factory=list)
    derived_fresh: int = 0
    pruned_derived: int = 0


class VerdictOut(BaseModel):
    committed: bool
    reason: str = ""
    agent: str = ""
    action: str = ""
    entity_id: str = ""


class ProposalsOut(BaseModel):
    revision: int
    verdicts: list[VerdictOut] = Field(default_factory=list)
    derived_fresh: int = 0
    pruned_derived: int = 0


class HealthOut(BaseModel):
    ok: bool = True
    nodes: int = 0
    edges: int = 0
    revision: int = 0


class GraphOut(BaseModel):
    elements: list[dict[str, Any]] = Field(default_factory=list)
    revision: int = 0
    # Persisted first-class nodes only; folded Subject/Market nodes are
    # synthesized into `elements` on read and counted separately.
    nodes_count: int = 0
    # Persisted primitive edges only; folded/derived links are added to
    # `elements` on read and counted separately.
    edges_count: int = 0
    derived_count: int = 0
    # Subject/Market nodes synthesized from props on read.
    derived_nodes_count: int = 0


class GraphService:
    """Thread-safe owner of the curated graph + its log/snapshot."""

    def __init__(self, snapshot_path: str = "data_local/graph_snapshot.json",
                 log_path: str = "data_local/graph_log.jsonl",
                 findings_path: str = "data_local/findings.jsonl") -> None:
        self.snapshot_path = snapshot_path
        self.log_path = log_path
        self.findings_path = findings_path
        self.lock = threading.Lock()
        self.api = boot(snapshot_path, log_path)

    def health(self) -> dict:
        with self.lock:
            return {"ok": True, "nodes": len(self.api.store.nodes),
                    "edges": len(self.api.store.edges),
                    "revision": self.api.revision}

    @staticmethod
    def _expand_sets(expand: str | set[str] | None) -> tuple[set[str], set[str]]:
        """(node kinds, edge rels) implied by an expand param."""
        if isinstance(expand, str):
            parts = {p.strip().lower() for p in expand.split(",") if p.strip()}
        else:
            parts = {str(p).lower() for p in (expand or set())}
        if "all" in parts:
            return ({"Subject", "Market"},
                    {"mentions", "shares-subject-with", "has-market"})
        kinds: set[str] = set()
        rels: set[str] = set()
        if "mentions" in parts:
            kinds.add("Subject")
            rels.add("mentions")
        if "markets" in parts or "has-market" in parts:
            kinds.add("Market")
            rels.add("has-market")
        if "mesh" in parts or "shares-subject-with" in parts:
            rels.add("shares-subject-with")
        return (kinds, rels)

    def graph_payload(self, at: str | None = None,
                      expand: str | set[str] | None = None) -> dict:
        kinds, rels = self._expand_sets(expand)
        with self.lock:
            store = self.api.store
            if at:
                store = subgraph_as_of(store, at)
            stored_nodes = list(store.nodes.values())
            edges = list(store.edges)
            if kinds or rels:
                made_nodes = [n for n in materialize_derived_nodes(
                    store, kinds=kinds or set()) if n.id not in store.nodes]
                nodes = stored_nodes + made_nodes
                derived = materialize_derived_links(store, rels=rels)
            else:
                made_nodes = []
                nodes = stored_nodes
                derived = []
            rev = self.api.revision
        # Legacy snapshots may still hold derived edges. Merge the
        # read-time set without duplicating keys already persisted.
        seen = {(e.src, e.dst, e.rel) for e in edges}
        materialized = [e for e in derived
                        if (e.src, e.dst, e.rel) not in seen]
        payload = to_cytoscape(nodes, edges + materialized, limit=None)
        payload["revision"] = rev
        payload["nodes_count"] = len(stored_nodes)
        payload["edges_count"] = len(edges)
        payload["derived_count"] = len(materialized)
        payload["derived_nodes_count"] = len(made_nodes)
        return payload

    def submit(self, proposals: list[dict]) -> dict:
        with self.lock:
            result = commit_batch(self.api, proposals,
                                  log_path=self.log_path,
                                  snapshot_path=self.snapshot_path)
            verdicts = result["verdicts"]
            n_ok = sum(1 for v in verdicts if v.get("committed"))
            print(f"[graph-api] proposals={len(verdicts)} "
                  f"committed={n_ok} rejected={len(verdicts) - n_ok} "
                  f"derived_fresh={result['derived_fresh']} "
                  f"pruned_derived={result.get('pruned_derived', 0)} "
                  f"revision={result['revision']}", flush=True)
            return {"revision": result["revision"],
                    "verdicts": result["verdicts"],
                    "derived_fresh": result["derived_fresh"],
                    "pruned_derived": result.get("pruned_derived", 0)}

    def record_finding(self, finding: dict) -> dict:
        rec = append_finding(self.findings_path, finding)
        print(f"[graph-api] finding recorded_at={rec.get('recorded_at')}",
              flush=True)
        return rec

    def retire(self, node_ids: list[str]) -> dict:
        with self.lock:
            result = commit_retire(self.api, node_ids,
                                   log_path=self.log_path,
                                   snapshot_path=self.snapshot_path)
            print(f"[graph-api] retire={len(result['retired'])} "
                  f"pruned_derived={result.get('pruned_derived', 0)} "
                  f"revision={result['revision']}", flush=True)
            return result


def create_app(service: GraphService | None = None,
               snapshot_path: str = "data_local/graph_snapshot.json",
               log_path: str = "data_local/graph_log.jsonl",
               findings_path: str = "data_local/findings.jsonl") -> FastAPI:
    svc = service or GraphService(snapshot_path=snapshot_path,
                                  log_path=log_path,
                                  findings_path=findings_path)
    app = FastAPI(title="Compass graph API")
    app.state.service = svc

    @app.get("/health", response_model=HealthOut)
    def health() -> dict:
        return svc.health()

    @app.get("/graph", response_model=GraphOut)
    def read_graph(request: Request, response: Response,
                   at: str | None = None, expand: str | None = None):
        payload = svc.graph_payload(at=at, expand=expand)
        response.headers["ETag"] = str(payload["revision"])
        if (request.headers.get("if-none-match") or "").strip() == str(
                payload["revision"]):
            print(f"[graph-api] GET /graph rev={payload['revision']} "
                  f"unchanged -> 304", flush=True)
            return Response(status_code=304)
        return payload

    @app.post("/proposals", response_model=ProposalsOut)
    def post_proposals(body: ProposalsIn) -> dict:
        norm: list[dict] = []
        for p in body.proposals:
            q = p.model_dump()
            if not q.get("agent") and body.agent:
                q["agent"] = body.agent
            norm.append(q)
        return svc.submit(norm)

    @app.post("/retire", response_model=RetireOut)
    def post_retire(body: RetireIn) -> dict:
        return svc.retire(body.node_ids)

    @app.post("/findings")
    def post_findings(body: dict) -> dict:
        finding = body.get("finding", body)
        if not isinstance(finding, dict):
            finding = {"value": finding}
        rec = svc.record_finding(finding)
        return {"ok": True, "recorded_at": rec["recorded_at"]}

    return app


def _resolve(base: str, path: str) -> str:
    import os

    if os.path.isabs(path):
        return path
    return os.path.join(base, path)


@click.command()
@click.option("--port", type=int, default=8090)
@click.option("--snapshot", default="graph_snapshot.json")
@click.option("--log", default="graph_log.jsonl")
@click.option("--findings", default="findings.jsonl")
@click.option("--data-dir", default="data_local", envvar="COMPASS_DATA_DIR",
              help="state directory (COMPASS_DATA_DIR also works)")
def main(port, snapshot, log, findings, data_dir) -> None:
    import uvicorn

    app = create_app(snapshot_path=_resolve(data_dir, snapshot),
                     log_path=_resolve(data_dir, log),
                     findings_path=_resolve(data_dir, findings))
    svc: GraphService = app.state.service
    print(f"[graph-api] nodes={len(svc.api.store.nodes)} "
          f"edges={len(svc.api.store.edges)} "
          f"revision={svc.api.revision} port={port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info",
                access_log=True)


if __name__ == "__main__":
    main()
