"""Backfill closed hypothesis types onto typeless Hypothesis nodes.

Reads the graph, and for every Hypothesis node without props.type posts
an investigator add-node proposal carrying just {type}; the gate merges
props into the existing node (duplicate -> refreshed), so existing
props (mode, evidence, layer, via) and titles are preserved. Idempotent:
a second run finds nothing to do.

Type rule: normalize the node's stored mode (co-mention ->
co_mention, ...); unknown/missing modes fall back to co_mention.

Run:  uv run python scripts/backfill_hypothesis_type.py --dry-run
      uv run python scripts/backfill_hypothesis_type.py
      uv run python scripts/backfill_hypothesis_type.py --graph-url URL --batch 25
"""

import json
import sys
import urllib.request

import click

sys.path.insert(0, ".")

from squad.auditor import normalize_hypothesis_type  # noqa: E402


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "compass/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def backfill_type_proposals(elements: list) -> list[dict]:
    """Proposals adding {type} to typeless Hypothesis nodes.

    Pure: [{entity_id, title, htype}] for nodes missing props.type.
    Type is the normalized stored mode, defaulting to co_mention.
    Title is the stored label (the gate requires a non-empty title;
    same title + merged props refreshes in place).
    """
    out = []
    for el in elements:
        d = el.get("data", {})
        if "source" in d or d.get("kind") != "Hypothesis":
            continue
        props = d.get("props", {}) or {}
        if props.get("type"):
            continue
        htype = normalize_hypothesis_type(props.get("mode")) or "co_mention"
        hid = d.get("id", "")
        out.append({"entity_id": hid,
                    "title": d.get("label", "") or hid,
                    "htype": htype})
    out.sort(key=lambda p: p["entity_id"])
    return out


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--batch", type=int, default=25,
              help="proposals per POST")
@click.option("--dry-run", is_flag=True, default=False,
              help="list what would change, post nothing")
def main(graph_url, batch, dry_run) -> None:
    try:
        data = _get_json(graph_url.rstrip("/") + "/graph")
    except Exception as e:
        raise SystemExit(f"graph API unreachable at {graph_url}: {e}")
    todo = backfill_type_proposals(data.get("elements", []))
    print(f"[backfill-types] {len(todo)} typeless hypotheses", flush=True)
    if dry_run:
        for p in todo[:20]:
            print(f"  {p['entity_id']} -> {p['htype']}", flush=True)
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more", flush=True)
        return
    proposals = [
        {"agent": "investigator", "action": "add-node",
         "entity_id": p["entity_id"],
         "detail": json.dumps({"kind": "Hypothesis",
                               "title": p["title"],
                               "props": {"type": p["htype"]}}),
         "confidence": 0.8}
        for p in todo
    ]
    tallies: dict = {}
    rev = None
    for i in range(0, len(proposals), batch):
        posted = _post_json(graph_url.rstrip("/") + "/proposals",
                            {"proposals": proposals[i:i + batch]})
        rev = posted.get("revision", rev)
        for v in posted.get("verdicts", []):
            tallies[v.get("reason", "?")] = tallies.get(v.get("reason", "?"), 0) + 1
    print(f"[backfill-types] posted {len(proposals)} rev={rev} {tallies}", flush=True)


if __name__ == "__main__":
    main()
