"""One-time migration: type-qualify pair-hypothesis ids.

Rewrites legacy `hypothesis:loop-{a}-{b}` (and `odds-`/`xlink-`) ids to
`hypothesis:{prefix}-{a}-{b}-{type}`, using each node's stored
closed-taxonomy `props.type`, so a second detection mode creates a
sibling claim instead of refreshing the first in place. Edge endpoints
and `audit:{hid}` ids/targets are rewritten with them.

Dry run (default) writes <data-dir>/graph_snapshot.hids.json, boots it
in a temp dir, and verifies the read-time view matches the current
payload apart from the renames. Nothing live is touched.

--apply backs up the snapshot, moves the commit log into
backup-graph-<utc>/, swaps in the migrated snapshot, and starts a fresh
log. Stop the graph API first: it owns the in-memory store and would
overwrite the snapshot on its next batch. The script refuses --apply
while :8090 answers (override with --api-check "").

Run:  uv run python scripts/migrate_hypothesis_ids.py            # dry run
      uv run python scripts/migrate_hypothesis_ids.py --apply    # migrate
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
    GraphStore, boot, materialize_derived_links, materialize_derived_nodes,
)

PAIR_PREFIXES = ("loop", "odds", "xlink")
PAIR_TYPES = ("co_mention", "co_location", "shared_context", "co_outcome")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _get_json(url: str, timeout: int = 5) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "compass/0.1"})
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
            "migration": "type-qualified-pair-hypothesis-ids",
            "proposals": [], "verdicts": [], "retired": []}


def _pair_hypothesis_ids(store: GraphStore) -> list[str]:
    """Legacy pair-hypothesis ids (any prefix), migrated or not."""
    out = []
    for nid, node in store.nodes.items():
        if node.kind != "Hypothesis" or not nid.startswith("hypothesis:"):
            continue
        rest = nid.removeprefix("hypothesis:")
        if rest.split("-", 1)[0] in PAIR_PREFIXES:
            out.append(nid)
    return sorted(out)


def _new_id(nid: str, node) -> str | None:
    """Qualified id, or None when already migrated."""
    htype = (node.props or {}).get("type") or "co_mention"
    if htype not in PAIR_TYPES:
        htype = "co_mention"
    if nid.endswith("-" + htype):
        return None
    return f"{nid}-{htype}"


def migrate_store(store: GraphStore) -> tuple[GraphStore, dict]:
    """Rename pair-hypothesis ids; return (new store, report)."""
    rename: dict[str, str] = {}
    for nid in _pair_hypothesis_ids(store):
        new = _new_id(nid, store.nodes[nid])
        if new is not None:
            rename[nid] = new
    if len(set(rename.values())) != len(rename):
        dupes = sorted(n for n in Counter(rename.values()).items()
                       if n[1] > 1)
        raise SystemExit(f"rename collides: {dupes[:5]}")

    # Audit ids embed the hypothesis id; rename those endpoints too.
    for nid in list(store.nodes):
        if nid.startswith("audit:") and nid.removeprefix("audit:") in rename:
            rename[nid] = f"audit:{rename[nid.removeprefix('audit:')]}"

    nodes: dict = {}
    renamed_nodes = 0
    for nid, node in store.nodes.items():
        if nid in rename and not nid.startswith("audit:"):
            nodes[rename[nid]] = replace(node, id=rename[nid])
            renamed_nodes += 1
        elif nid in rename:  # audit node
            props = dict(node.props or {})
            if props.get("target") in rename:
                props["target"] = rename[props["target"]]
            nodes[rename[nid]] = replace(node, id=rename[nid], props=props)
            renamed_nodes += 1
        else:
            nodes[nid] = node

    edges = []
    rewritten = 0
    for edge in store.edges:
        src = rename.get(edge.src, edge.src)
        dst = rename.get(edge.dst, edge.dst)
        if (src, dst) != (edge.src, edge.dst):
            rewritten += 1
            edges.append(replace(edge, src=src, dst=dst))
        else:
            edges.append(edge)

    migrated = GraphStore(nodes=nodes, edges=edges)
    by_prefix = Counter(nid.split(":")[1].split("-")[0]
                        for nid in rename if not nid.startswith("audit:"))
    return migrated, {
        "rev": None,  # filled by caller
        "renamed": renamed_nodes,
        "renamed_edges": rewritten,
        "by_prefix": dict(by_prefix),
        "before": {"nodes": len(store.nodes), "edges": len(store.edges)},
        "after": {"nodes": len(nodes), "edges": len(edges)},
    }


def _view(store: GraphStore) -> tuple[list, list]:
    nodes = list(store.nodes.values())
    nodes += [n for n in materialize_derived_nodes(store)
              if n.id not in store.nodes]
    known = {(e.src, e.dst, e.rel) for e in store.edges}
    edges = list(store.edges)
    edges += [e for e in materialize_derived_links(store)
              if (e.src, e.dst, e.rel) not in known]
    return nodes, edges


def validate(store: GraphStore, original: GraphStore, report: dict) -> None:
    """Invariants that must hold before anything is written live."""
    problems: list[str] = []
    for nid in _pair_hypothesis_ids(store):
        node = store.nodes[nid]
        htype = (node.props or {}).get("type") or "co_mention"
        if htype not in PAIR_TYPES:
            problems.append(f"unknown hypothesis type: {nid}")
        elif not nid.endswith("-" + htype):
            problems.append(f"unqualified pair id remains: {nid}")
            break
    if len(store.nodes) != len(original.nodes):
        problems.append("node count changed")
    if len(store.edges) != len(original.edges):
        problems.append("edge count changed")
    keys = [(e.src, e.dst, e.rel) for e in store.edges]
    if len(keys) != len(set(keys)):
        problems.append("duplicate edges remain")
    visible = set(store.nodes) | {n.id for n in materialize_derived_nodes(store)}
    dangling = sorted({(e.src, e.dst) for e in store.edges
                       if e.src not in visible or e.dst not in visible})
    if dangling:
        problems.append(f"dangling edges: {dangling[:5]}")
    if problems:
        raise SystemExit("migration validation failed:\n  "
                         + "\n  ".join(problems))


def compare_views(original: GraphStore, migrated: GraphStore,
                  rename: dict[str, str]) -> dict:
    """Read-time view must match, modulo the renames."""
    before_nodes, before_edges = _view(original)
    after_nodes, after_edges = _view(migrated)
    back = {new: old for old, new in rename.items()}
    # Audit ids embed the hypothesis id; map those back too.
    back.update({f"audit:{new}": f"audit:{old}"
                 for old, new in rename.items()})

    def norm(nid: str) -> str:
        return back.get(nid, nid)

    bn = {norm(n.id): (n.kind, n.title) for n in before_nodes}
    an = {norm(n.id): (n.kind, n.title) for n in after_nodes}
    be = {(norm(e.src), norm(e.dst), e.rel) for e in before_edges}
    ae = {(norm(e.src), norm(e.dst), e.rel) for e in after_edges}

    problems: list[str] = []
    if set(bn) != set(an):
        problems.append(
            f"node id set changed beyond renames: "
            f"{sorted(set(bn) ^ set(an))[:5]}")
    mismatched = {i: (bn[i], an[i]) for i in set(bn) & set(an)
                  if bn[i] != an[i]}
    if mismatched:
        problems.append(f"node kind/title changed: {list(mismatched)[:5]}")
    if be != ae:
        problems.append(
            f"edge set changed beyond renames: "
            f"{sorted((be ^ ae))[:5]}")
    if problems:
        raise SystemExit("view comparison failed:\n  "
                         + "\n  ".join(problems))
    return {
        "view_nodes": len(bn),
        "view_edges": len(be),
        "renamed": len(rename),
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
    preview = f"{data_dir}/graph_snapshot.hids.json"

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
    rename = {}
    for nid in _pair_hypothesis_ids(original):
        new = _new_id(nid, original.nodes[nid])
        if new is not None:
            rename[nid] = new
    # Audit ids embed hypothesis ids; include them in the view mapping.
    for nid in original.nodes:
        if nid.startswith("audit:") and nid.removeprefix("audit:") in rename:
            rename[nid] = f"audit:{rename[nid.removeprefix('audit:')]}"
    check = compare_views(original, migrated, rename)

    if not report["renamed"]:
        print("[migrate-hids] already migrated; nothing to do", flush=True)
        return

    out = {"revision": revision, "store": migrated.to_json()}

    with tempfile.TemporaryDirectory() as td:
        tsp = os.path.join(td, "graph_snapshot.json")
        tlp = os.path.join(td, "graph_log.jsonl")
        _write_json(tsp, out)
        with open(tlp, "w") as f:
            f.write(json.dumps(_marker(revision)) + "\n")
        preview_api = boot(tsp, tlp)
        if preview_api.revision != revision:
            raise SystemExit("preview boot revision mismatch")
        if preview_api.store.to_json() != migrated.to_json():
            raise SystemExit("preview boot store mismatch")

    _write_json(preview, out)

    print(f"[migrate-hids] rev={revision} "
          f"before: {report['before']['nodes']} nodes / "
          f"{report['before']['edges']} edges", flush=True)
    print(f"[migrate-hids] rename: {report['renamed']} nodes "
          f"({report['by_prefix']}), {report['renamed_edges']} edge "
          f"endpoints rewritten", flush=True)
    print(f"[migrate-hids] read view: {check['view_nodes']} nodes / "
          f"{check['view_edges']} edges, unchanged apart from renames",
          flush=True)
    print(f"[migrate-hids] preview: {preview}", flush=True)

    if not apply:
        print("[migrate-hids] dry run — pass --apply to swap it in",
              flush=True)
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
        "purpose": "pre-migration graph (unqualified pair-hypothesis ids)",
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

    print(f"[migrate-hids] applied; backup: {backup}", flush=True)
    print(f"[migrate-hids] live snapshot rev={revision} with a fresh log")

if __name__ == "__main__":
    main()
