"""One-time migration: fold Subject/Market nodes into props.

Rewrites a legacy snapshot into the primitive-only shape:

- Market nodes fold into Event.props.markets[ticker]
- Subject nodes fold into Article.props.subjects
- mentions / has-market / shares-subject-with edges are dropped (the read
  path reconstructs them)
- layer-2 transitive Hypothesis nodes and their incident edges are dropped
- legacy market -> happening `relates-to` edges move onto the owning Event

Dry run (default) writes <data-dir>/graph_snapshot.migrated.json, boots it
in a temp dir, and verifies that the read-time view matches the current
payload apart from the intentional drops. Nothing live is touched.

--apply backs up the snapshot, moves the commit log into
backup-graph-<utc>/, swaps in the migrated snapshot, and starts a fresh
log. Stop the graph API first: it owns the in-memory store and would
overwrite the snapshot on its next batch. The script refuses --apply while
:8090 answers (override with --api-check "").

Run:  uv run python scripts/migrate_graph.py            # dry run
      uv run python scripts/migrate_graph.py --apply    # migrate
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

import click

sys.path.insert(0, ".")

from graph import (  # noqa: E402
    MATERIALIZED_RELS, PRIMITIVE_RELS, GraphStore, boot,
    materialize_derived_links, materialize_derived_nodes,
)
from graph.store import FOLDED_NODE_KINDS, subject_id  # noqa: E402


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _get_json(url: str, timeout: int = 5) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "compass-migrate/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, path)


def _unique_dir(path: str) -> str:
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists(f"{path}-{n}"):
        n += 1
    return f"{path}-{n}"


def _marker(revision: int) -> dict:
    return {"revision": revision, "recorded_at": _utcnow(),
            "migration": "fold-subject-market-to-props",
            "proposals": [], "verdicts": [], "retired": []}


def _owner_events(store: GraphStore) -> dict[str, str]:
    """market node id -> owning Event id (event_ticker, then prefix)."""
    events = sorted(
        (n.id for n in store.nodes.values() if n.kind == "Event"),
        key=lambda e: (-len(e), e))
    eventset = set(events)
    out: dict[str, str] = {}
    for nid, node in store.nodes.items():
        if node.kind != "Market":
            continue
        ticker = nid.removeprefix("market:")
        event = (node.props or {}).get("event_ticker")
        if event and f"kalshi:{event}" in eventset:
            out[nid] = f"kalshi:{event}"
            continue
        for eid in events:
            et = eid.removeprefix("kalshi:")
            if ticker == et or ticker.startswith(et + "-"):
                out[nid] = eid
                break
    return out


def _meta_ids(store: GraphStore) -> set[str]:
    return {n.id for n in store.nodes.values()
            if n.kind == "Hypothesis" and (
                n.id.startswith("hypothesis:loop-meta")
                or (n.props or {}).get("layer") == 2
                or (n.props or {}).get("mode") == "transitive")}


def _merge_terms(existing, incoming) -> list[str]:
    out: list[str] = []
    for source in (existing, incoming):
        if isinstance(source, str):
            source = [source]
        for term in source or []:
            if isinstance(term, str) and term and term not in out:
                out.append(term)
    return out


def _fold_subject_term(edge_dst: str, node) -> str:
    """Prefer the human title, but keep the legacy id if it would change."""
    if node is not None and node.kind == "Subject":
        term = node.title or edge_dst.removeprefix("subject:")
        return term if subject_id(term) == edge_dst else edge_dst
    return edge_dst


def migrate_store(store: GraphStore) -> tuple[GraphStore, dict]:
    """Fold folded kinds/rels into props; return (new store, report)."""
    owners = _owner_events(store)
    metas = _meta_ids(store)
    markets = {i: n for i, n in store.nodes.items() if n.kind == "Market"}
    subjects = {i: n for i, n in store.nodes.items() if n.kind == "Subject"}

    unattributed = sorted(i for i in markets if i not in owners)
    if unattributed:
        raise SystemExit(
            "cannot fold markets with no owning Event: "
            + ", ".join(unattributed[:10])
            + (f" (+{len(unattributed) - 10} more)"
               if len(unattributed) > 10 else ""))

    market_props: dict[str, dict[str, dict]] = {}
    for mid, node in markets.items():
        props = dict(node.props or {})
        props["title"] = node.title or mid.removeprefix("market:")
        market_props.setdefault(owners[mid], {})[
            mid.removeprefix("market:")] = props

    article_terms: dict[str, list[str]] = {}
    mentioned: set[str] = set()
    for edge in store.edges:
        if edge.rel != "mentions":
            continue
        mentioned.add(edge.dst)
        node = store.nodes.get(edge.dst)
        if node is not None and node.kind != "Subject":
            continue
        if not edge.dst.startswith("subject:"):
            continue
        article_terms.setdefault(edge.src, []).append(
            _fold_subject_term(edge.dst, node))

    nodes: dict = {}
    dropped_nodes = Counter()
    folded_markets = 0
    folded_articles = 0
    for nid, node in store.nodes.items():
        if node.kind in FOLDED_NODE_KINDS:
            dropped_nodes[node.kind] += 1
            continue
        if nid in metas:
            dropped_nodes["layer2"] += 1
            continue
        props = dict(node.props or {})
        if node.kind == "Event" and nid in market_props:
            existing = props.get("markets")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(market_props[nid])
            props["markets"] = merged
            folded_markets += len(market_props[nid])
        if node.kind == "Article" and nid in article_terms:
            props["subjects"] = _merge_terms(props.get("subjects"),
                                             article_terms[nid])
            folded_articles += 1
        nodes[nid] = (replace(node, props=props)
                      if props != (node.props or {}) else node)

    edges = []
    seen: set[tuple[str, str, str]] = set()
    dropped_edges = Counter()
    meta_edges: set[tuple[str, str, str]] = set()
    rewritten = 0
    for edge in store.edges:
        if edge.rel in MATERIALIZED_RELS:
            dropped_edges[edge.rel] += 1
            continue
        if edge.src in metas or edge.dst in metas:
            meta_edges.add((edge.src, edge.dst, edge.rel))
            dropped_edges[f"{edge.rel} (layer2)"] += 1
            continue
        src, dst = edge.src, edge.dst
        if edge.rel == "relates-to":
            if src in owners:
                src = owners[src]
                rewritten += 1
            if dst in owners:
                dst = owners[dst]
                rewritten += 1
        key = (src, dst, edge.rel)
        if key in seen:
            dropped_edges["duplicate"] += 1
            continue
        seen.add(key)
        edges.append(replace(edge, src=src, dst=dst)
                     if (src, dst) != (edge.src, edge.dst) else edge)

    orphan_subjects = sorted(i for i in subjects if i not in mentioned)
    migrated = GraphStore(nodes=nodes, edges=edges)
    report = {
        "rev": None,  # filled by caller
        "before": {"nodes": len(store.nodes), "edges": len(store.edges)},
        "folded": {
            "markets": len(markets),
            "market_props": folded_markets,
            "subjects": len(subjects),
            "articles_with_subjects": folded_articles,
        },
        "dropped": dict(dropped_nodes), "dropped_edges": dict(dropped_edges),
        "rewritten_relates_to": rewritten,
        "orphan_subjects": orphan_subjects,
        "meta_ids": sorted(metas),
        "meta_edges": sorted(meta_edges),
        "attributes_missing_events": unattributed,
        "after": {"nodes": len(nodes), "edges": len(edges)},
    }
    return migrated, report


def _view(store: GraphStore) -> tuple[list, list]:
    nodes = list(store.nodes.values())
    nodes += [n for n in materialize_derived_nodes(store)
              if n.id not in store.nodes]
    known = {(e.src, e.dst, e.rel) for e in store.edges}
    edges = list(store.edges)
    edges += [e for e in materialize_derived_links(store)
              if (e.src, e.dst, e.rel) not in known]
    return nodes, edges


def _sig(nodes, edges, owners: dict[str, str]):
    node_sig = {n.id: (n.kind, n.title) for n in nodes}
    edge_sig = set()
    for edge in edges:
        src, dst = edge.src, edge.dst
        if edge.rel == "shares-subject-with":
            src, dst = sorted((src, dst))
        if edge.rel == "relates-to":
            src, dst = owners.get(src, src), owners.get(dst, dst)
        edge_sig.add((src, dst, edge.rel))
    return node_sig, edge_sig


def validate(store: GraphStore, original: GraphStore, report: dict) -> None:
    """Invariants that must hold before anything is written live."""
    problems: list[str] = []
    folded = [n.id for n in store.nodes.values()
              if n.kind in FOLDED_NODE_KINDS]
    if folded:
        problems.append(f"folded nodes remain: {len(folded)}")
    bad_rels = sorted({e.rel for e in store.edges} - set(PRIMITIVE_RELS))
    if bad_rels:
        problems.append(f"non-primitive rels remain: {bad_rels}")
    visible = set(store.nodes) | {n.id for n in materialize_derived_nodes(store)}
    dangling = sorted({(e.src, e.dst) for e in store.edges
                       if e.src not in visible or e.dst not in visible})
    if dangling:
        problems.append(f"dangling edges: {dangling[:5]}")
    keys = [(e.src, e.dst, e.rel) for e in store.edges]
    if len(keys) != len(set(keys)):
        problems.append("duplicate edges remain")

    owners = _owner_events(original)
    for mid in (i for i, n in original.nodes.items() if n.kind == "Market"):
        owner = store.nodes.get(owners.get(mid, ""))
        markets = (owner.props or {}).get("markets") if owner else None
        ticker = mid.removeprefix("market:")
        if not isinstance(markets, dict) or ticker not in markets:
            problems.append(f"market not folded: {mid}")
            break

    folded_subjects = set()
    for node in store.nodes.values():
        if node.kind != "Article":
            continue
        for term in (node.props or {}).get("subjects") or []:
            if isinstance(term, str):
                folded_subjects.add(term if term.startswith("subject:")
                                    else subject_id(term))
    expected_subjects = {i for i, n in original.nodes.items()
                         if n.kind == "Subject"}
    expected_subjects -= set(report["orphan_subjects"])
    missing_subjects = sorted(expected_subjects - folded_subjects)
    if missing_subjects:
        problems.append(f"subjects not folded: {missing_subjects[:5]}")

    if problems:
        raise SystemExit("migration validation failed:\n  "
                         + "\n  ".join(problems))


def compare_views(original: GraphStore, migrated: GraphStore) -> dict:
    """Read-time view must match, apart from the intentional drops."""
    before_nodes, before_edges = _view(original)
    after_nodes, after_edges = _view(migrated)
    owners = _owner_events(original)
    bn, be = _sig(before_nodes, before_edges, owners)
    an, ae = _sig(after_nodes, after_edges, owners)
    metas = _meta_ids(original)
    expected_missing_nodes = metas
    expected_missing_edges = {(e.src, e.dst, e.rel)
                              for e in original.edges
                              if e.src in metas or e.dst in metas}

    problems: list[str] = []
    if set(bn) - expected_missing_nodes != set(an):
        problems.append("node id set changed beyond layer-2 metas")
    extra = set(an) - set(bn)
    if extra:
        problems.append(f"new nodes appeared: {sorted(extra)[:5]}")
    mismatched = {i: (bn[i], an[i]) for i in set(bn) & set(an)
                  if bn[i] != an[i]}
    if mismatched:
        problems.append(f"node kind/title changed: {list(mismatched)[:5]}")
    if be - ae != expected_missing_edges:
        problems.append(f"edges lost beyond layer-2: "
                        f"{sorted((be - ae) - expected_missing_edges)[:5]}")
    if ae - be:
        problems.append(f"edges gained: {sorted(ae - be)[:5]}")

    after_market_props = {n.id: dict(n.props) for n in after_nodes
                          if n.kind == "Market"}
    for mid, node in original.nodes.items():
        if node.kind != "Market":
            continue
        got = after_market_props.get(mid)
        expected = {**dict(node.props or {}), "title": node.title}
        if got != expected:
            problems.append(f"market props changed: {mid}")
            break

    if problems:
        raise SystemExit("view comparison failed:\n  "
                         + "\n  ".join(problems))

    return {
        "view_nodes_before": len(bn),
        "view_nodes_after": len(an),
        "view_edges_before": len(be),
        "view_edges_after": len(ae),
        "mesh_before": sum(1 for _, _, r in be
                           if r == "shares-subject-with"),
        "mesh_after": sum(1 for _, _, r in ae
                          if r == "shares-subject-with"),
        "expected_missing_nodes": sorted(expected_missing_nodes),
        "expected_missing_edges": len(expected_missing_edges),
    }


@click.command()
@click.option("--data-dir", default="data_local")
@click.option("--apply", is_flag=True, default=False,
              help="back up, swap in the migrated snapshot, rotate the log")
@click.option("--api-check", default="http://127.0.0.1:8090/health",
              help='refuse --apply while this answers ("" to skip)')
def main(data_dir, apply, api_check) -> None:
    data_dir = data_dir.rstrip("/")
    snap = f"{data_dir}/graph_snapshot.json"
    log = f"{data_dir}/graph_log.jsonl"
    preview = f"{data_dir}/graph_snapshot.migrated.json"

    if apply and api_check:
        try:
            _get_json(api_check)
            raise SystemExit(
                "graph API is running — stop it first so its next "
                "batch cannot overwrite the migration "
                '(or pass --api-check "")')
        except SystemExit:
            raise
        except Exception:
            pass

    if not os.path.exists(snap):
        raise SystemExit(f"missing snapshot: {snap}")

    # The service boots snapshot + replay; migrate the same effective
    # graph so log-only commits are not lost.
    api = boot(snap, log)
    revision = api.revision
    original = api.store

    migrated, report = migrate_store(original)
    report["rev"] = revision
    validate(migrated, original, report)
    check = compare_views(original, migrated)

    unchanged = (not report["folded"]["markets"]
                 and not report["folded"]["subjects"]
                 and not report["dropped"]
                 and not report["dropped_edges"])
    if unchanged:
        print("[migrate] already migrated; nothing to do", flush=True)
        return

    out = {"revision": revision, "store": migrated.to_json()}

    with tempfile.TemporaryDirectory() as td:
        tsp = os.path.join(td, "graph_snapshot.json")
        tlp = os.path.join(td, "graph_log.jsonl")
        _write_json(tsp, out)
        with open(tlp, "w") as f:
            f.write(json.dumps(_marker(revision)) + "\n")
        api = boot(tsp, tlp)
        if api.revision != revision:
            raise SystemExit("preview boot revision mismatch")
        if api.store.to_json() != migrated.to_json():
            raise SystemExit("preview boot store mismatch")

    _write_json(preview, out)

    folded, dropped = report["folded"], report["dropped"]
    print(f"[migrate] rev={revision} "
          f"before: {report['before']['nodes']} nodes / "
          f"{report['before']['edges']} edges", flush=True)
    print(f"[migrate] fold: {folded['markets']} markets -> "
          f"{len([n for n in migrated.nodes.values() if n.kind == 'Event' and (n.props or {}).get('markets')])} events, "
          f"{folded['subjects']} subjects -> "
          f"{folded['articles_with_subjects']} articles", flush=True)
    print(f"[migrate] drop nodes: {dropped or {}}", flush=True)
    print(f"[migrate] drop edges: {report['dropped_edges']}", flush=True)
    if report["rewritten_relates_to"]:
        print(f"[migrate] rewrite: "
              f"{report['rewritten_relates_to']} relates-to edge endpoints "
              "onto owning Event(s)", flush=True)
    if report["orphan_subjects"]:
        print(f"[migrate] warn: {len(report['orphan_subjects'])} unattached "
              "subject nodes dropped", flush=True)
    print(f"[migrate] after: {report['after']['nodes']} nodes / "
          f"{report['after']['edges']} edges", flush=True)
    print(f"[migrate] read view: {check['view_nodes_before']} -> "
          f"{check['view_nodes_after']} nodes, "
          f"{check['view_edges_before']} -> {check['view_edges_after']} "
          f"edges (mesh {check['mesh_before']} -> {check['mesh_after']}, "
          f"layer-2 removed {check['expected_missing_edges']} edges)",
          flush=True)
    print(f"[migrate] verified against the current read-time view", flush=True)
    print(f"[migrate] preview: {preview}", flush=True)

    if not apply:
        print("[migrate] dry run — pass --apply to swap it in", flush=True)
        return

    backup = _unique_dir(f"{data_dir}/backup-graph-{_stamp()}")
    os.makedirs(backup)
    shutil.copy2(snap, f"{backup}/graph_snapshot.json")
    had_log = os.path.exists(log)
    if had_log:
        shutil.move(log, f"{backup}/graph_log.jsonl")

    files = {"graph_snapshot.json": f"{backup}/graph_snapshot.json"}
    if had_log:
        files["graph_log.jsonl"] = f"{backup}/graph_log.jsonl"
    checksums = {name: {"bytes": os.path.getsize(path),
                        "sha256": _sha256(path)}
                 for name, path in files.items()}
    with open(f"{backup}/SHA256SUMS", "w") as f:
        for name, meta in checksums.items():
            f.write(f"{meta['sha256']}  {name}\n")
    manifest = {
        "created_utc": _utcnow(),
        "purpose": "pre-migration graph (Subject/Market nodes, folded/"
                   "derived edges, layer-2 metas still present)",
        "revision": revision,
        "files": checksums,
        "report": report,
        "restore": ["stop the graph service",
                    "cp graph_snapshot.json graph_log.jsonl ../",
                    "start the service"],
    }
    with open(f"{backup}/MANIFEST.json", "w") as f:
        json.dump(manifest, f, indent=2)

    _write_json(snap, out)
    with open(log, "w") as f:
        f.write(json.dumps(_marker(revision)) + "\n")

    live = boot(snap, log)
    if live.revision != revision or live.store.to_json() != migrated.to_json():
        raise SystemExit(
            f"post-apply boot mismatch — restore from {backup}")

    if os.path.exists(preview):
        os.remove(preview)

    print(f"[migrate] applied; backup: {backup}", flush=True)
    print(f"[migrate] live snapshot rev={revision} with a fresh log")
    print("[migrate] start the graph service; next batch is revision "
          f"{revision + 1}", flush=True)


if __name__ == "__main__":
    main()
