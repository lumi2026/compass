"""Append-only persistence for the living graph (storage v1).

Three files, all under data/ by default:

- graph_log.jsonl: one record per committed batch. This IS the
  time-travel source and the audit trail; history is never rewritten.
- findings.jsonl: raw agent findings before gating (high volume stays
  out of the graph log).
- graph_snapshot.json: curated store checkpoint, atomically rewritten
  (temp + rename) after each batch and on shutdown.

Boot = load snapshot, replay log records after its revision. Edge
re-lays stay idempotent through the gate (reinforcement, not dupes),
so replay is safe. SQLite is the later migration, not step 1.
"""

import json
import os
from datetime import datetime, timezone

from .derive import refresh_derived_links
from .gate import GraphUpdateAPI, proposals_to_commit


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: str, record: dict) -> None:
    """Crash-safe append of one JSON record (creates parent dirs)."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_finding(path: str, finding: dict) -> dict:
    """Store one raw (ungated) finding; returns the stored record."""
    record = {"recorded_at": _utcnow(), "finding": finding}
    append_jsonl(path, record)
    return record


def commit_batch(api: GraphUpdateAPI, proposals: list[dict],
                 log_path: str | None = None,
                 snapshot_path: str | None = None,
                 snapshot_every: int = 1) -> dict:
    """Gate one batch through Skeptic lanes, log it, checkpoint it.

    Each proposal: {agent, action, entity_id, detail, confidence?}.
    Confidence gate mirrors Squad Skeptic (min 0.3): weak proposals
    are vetoed before they reach the graph. Returns
    {revision, verdicts: [{committed, reason, ...proposal}]}.
    """
    from squad import Skeptic  # lazy: squad imports graph types

    verdicts: list[dict] = []
    for p in proposals:
        agent = str(p.get("agent", ""))
        action = str(p.get("action", ""))
        entity_id = str(p.get("entity_id", ""))
        detail = p.get("detail", "")
        try:
            conf = float(p.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.0
        if not entity_id or not action or conf < Skeptic().min_confidence:
            verdicts.append({"committed": False,
                             "reason": "vetoed: weak proposal",
                             "agent": agent, "action": action,
                             "entity_id": entity_id})
            continue
        result = proposals_to_commit(api, agent, action, entity_id, detail)
        verdicts.append({"committed": result.committed,
                         "reason": result.reason,
                         "agent": agent, "action": action,
                         "entity_id": entity_id})
    try:
        fresh = refresh_derived_links(api)
    except Exception:
        fresh = 0
    api.revision += 1
    record = {"revision": api.revision, "recorded_at": _utcnow(),
              "proposals": proposals, "verdicts": verdicts,
              "derived_fresh": fresh}
    if log_path:
        append_jsonl(log_path, record)
    if snapshot_path and api.revision % max(1, snapshot_every) == 0:
        api.save(snapshot_path)
    return {"revision": api.revision, "verdicts": verdicts,
            "derived_fresh": fresh}


def replay_log(api: GraphUpdateAPI, log_path: str,
               after_revision: int = 0) -> int:
    """Re-apply committed proposals from the log. Returns max revision."""
    top = after_revision
    try:
        f = open(log_path)
    except OSError:
        return top
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            try:
                rev = int(rec.get("revision", 0))
            except (TypeError, ValueError):
                continue
            if rev <= after_revision:
                top = max(top, rev)
                continue
            for p, v in zip(rec.get("proposals", []),
                            rec.get("verdicts", [])):
                if isinstance(v, dict) and v.get("committed"):
                    try:
                        proposals_to_commit(
                            api, str(p.get("agent", "")),
                            str(p.get("action", "")),
                            str(p.get("entity_id", "")),
                            p.get("detail", ""))
                    except Exception:
                        continue
            try:
                refresh_derived_links(api)
            except Exception:
                pass
            top = max(top, rev)
    api.revision = max(api.revision, top)
    return top


def boot(snapshot_path: str, log_path: str) -> GraphUpdateAPI:
    """Load snapshot, replay later log records. Missing files -> empty."""
    api = GraphUpdateAPI.load(snapshot_path)
    replay_log(api, log_path, after_revision=api.revision)
    return api
