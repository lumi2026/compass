"""Independent audit loop: re-examine unaudited hypotheses, forever.

Each pass audits only hypotheses with no verdict yet (no Audit
challenge on the graph, no entry in the state file), so a kept link
is never re-audited and newly committed hypotheses are picked up on
later passes. Fresh vetoes post Audit + `challenges` proposals;
keeps are recorded in the state file only. Nothing is ever deleted,
unless --prune is passed: then all vetoed hypotheses still on the
graph are retired (removed with their incident edges) via one
POST /retire call, with no model resolution and no re-audit.

Run:  uv run python scripts/audit_loop.py
      uv run python scripts/audit_loop.py --loop --interval-min 60
      uv run python scripts/audit_loop.py --prune --dry-run
"""

import datetime
import json
import os
import sys
import time
import urllib.request

import click

sys.path.insert(0, ".")

from squad.auditor import audit_pair  # noqa: E402
from squad.models import resolve_model  # noqa: E402


def log(msg: str) -> None:
    """Stage log, always to stdout (the loop's only log sink)."""
    print(f"[audit-loop] {msg}", flush=True)


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def select_candidates(elements: list, state: dict,
                      max_audits: int = 0) -> list[dict]:
    """Hypotheses with no verdict yet: no Audit challenge, no state entry.

    Returns [{hid, title, events: [{ticker, title}], articles: [title]}].
    max_audits <= 0 means no cap.
    """
    nodes = {}
    for el in elements:
        d = el.get("data", {})
        if "source" not in d:
            nodes[d.get("id")] = d
    challenged = set()
    for el in elements:
        d = el.get("data", {})
        if d.get("label") != "challenges":
            continue
        challenged.add(d.get("target", ""))
        src = nodes.get(d.get("source", ""), {})
        if src.get("props", {}).get("target"):
            challenged.add(src["props"]["target"])
    supports: dict[str, list[str]] = {}
    for el in elements:
        d = el.get("data", {})
        if d.get("label") == "supports":
            supports.setdefault(d.get("source", ""), []).append(
                d.get("target", ""))
    out = []
    for hid, n in nodes.items():
        if n.get("kind") != "Hypothesis":
            continue
        if hid in challenged or hid in state:
            continue
        ends = supports.get(hid, [])
        events = [{"ticker": t.removeprefix("kalshi:"),
                   "title": nodes.get(t, {}).get("label", t)}
                  for t in ends
                  if nodes.get(t, {}).get("kind") == "Event"]
        # A pairwise claim needs two endpoints; auditing a single-event
        # hypothesis with "?" placeholders produces a meaningless verdict.
        if len(events) < 2:
            continue
        articles = [nodes.get(t, {}).get("label", t) for t in ends
                    if nodes.get(t, {}).get("kind") == "Article"]
        out.append({"hid": hid, "title": n.get("label", hid),
                    "events": events, "articles": articles})
    out.sort(key=lambda c: c["hid"])
    return out if max_audits <= 0 else out[:max_audits]


def _challenge_proposals(hid: str, reason: str) -> list[dict]:
    aid = f"audit:{hid}"
    return [
        {"agent": "auditor", "action": "add-node", "entity_id": aid,
         "detail": json.dumps(
             {"kind": "Audit",
              "title": f"audit flags {hid}: {reason}",
              "props": {"target": hid, "verdict": "reject",
                        "reason": reason, "mode": "re-audit"}}),
         "confidence": 0.8},
        {"agent": "auditor", "action": "add-edge", "entity_id": aid,
         "detail": json.dumps({"src": aid, "dst": hid,
                               "rel": "challenges"}),
         "confidence": 0.8},
    ]


def select_vetoed(elements: list, state: dict) -> list[str]:
    """All vetoed hypotheses still on the graph: state vetoes plus
    Audit-challenged nodes, intersected with existing Hypothesis ids."""
    nodes = {}
    for el in elements:
        d = el.get("data", {})
        if "source" not in d:
            nodes[d.get("id")] = d
    challenged = set()
    for el in elements:
        d = el.get("data", {})
        if d.get("label") != "challenges":
            continue
        challenged.add(d.get("target", ""))
        src = nodes.get(d.get("source", ""), {})
        if src.get("props", {}).get("target"):
            challenged.add(src["props"]["target"])
    existing = {hid for hid, n in nodes.items()
                if n.get("kind") == "Hypothesis"}
    vetoed = {hid for hid in existing if hid in challenged}
    for hid, v in state.items():
        if (isinstance(v, dict) and v.get("verdict") == "veto"
                and hid in existing):
            vetoed.add(hid)
    return sorted(vetoed)


def audit_pass(graph_url: str, candidates: list[dict], model_obj,
               dry_run: bool = False, post_fn=None,
               prune: bool = False, retire_fn=None) -> dict:
    """Audit each candidate; fresh vetoes post challenges unless dry_run.

    With prune=True, vetoed hypotheses with a committed challenge are
    also retired via POST /retire (skipped on dry_run and transient
    verdicts). Programmatic use only: the CLI --prune flag takes the
    prune-all path in run_once and never calls this. Returns {kept,
    vetoed, posted, pruned, llm_calls, verdicts: {hid: {...}}}.
    """
    post_fn = post_fn or _post_json
    retire_fn = retire_fn or _post_json
    kept = vetoed = posted = pruned = llm = 0
    verdicts: dict = {}
    for c in candidates:
        ev = [t for t in c["articles"] if t][:5]
        tickers = [e["ticker"] for e in c["events"]]
        titles = [e["title"] for e in c["events"]]
        while len(tickers) < 2:
            tickers.append("?")
            titles.append("?")
        if model_obj is not None:
            llm += 1
            r = audit_pair(tickers[0], titles[0], tickers[1], titles[1],
                           ev, "re-audit", model_obj)
            keep, reason = r.keep, r.reason
        else:
            keep, reason = True, "no-model: keep (fail-open)"
        verdict = {"verdict": "keep" if keep else "veto",
                   "reason": reason, "at": _utcnow()}
        # A model/network failure is not a verdict: leave it unrecorded so
        # the next pass retries instead of permanently marking it kept.
        if reason.startswith("llm-error"):
            verdict["transient"] = True
        if keep:
            kept += 1
            log(f"keep {c['hid']}: {reason}")
        else:
            vetoed += 1
            log(f"veto {c['hid']}: {reason}")
            if not dry_run:
                resp = post_fn(graph_url.rstrip("/") + "/proposals",
                               {"proposals": _challenge_proposals(
                                   c["hid"], reason)})
                committed = sum(1 for v in resp.get("verdicts", [])
                                if v.get("committed"))
                posted += committed
                verdict["posted"] = committed
                # Both the Audit node and the challenges edge must land;
                # otherwise retry rather than recording a silent veto.
                if committed < 2:
                    verdict["transient"] = True
                elif prune:
                    try:
                        resp = retire_fn(
                            graph_url.rstrip("/") + "/retire",
                            {"node_ids": [c["hid"]]})
                    except Exception as e:
                        log(f"prune failed {c['hid']}: {e}")
                        verdict["pruned"] = False
                    else:
                        retired = resp.get("retired", [])
                        removed = any(
                            v.get("entity_id") == c["hid"]
                            and v.get("removed")
                            for v in retired)
                        verdict["pruned"] = bool(removed)
                        if removed:
                            pruned += 1
                            log(f"pruned {c['hid']}")
        verdicts[c["hid"]] = verdict
    return {"kept": kept, "vetoed": vetoed, "posted": posted,
            "pruned": pruned, "llm_calls": llm, "verdicts": verdicts}


def run_once(graph_url: str, model, max_audits: int,
             dry_run: bool = False,
             state_path: str = "data_local/audit_state.json",
             post_fn=None, prune: bool = False,
             retire_fn=None) -> dict:
    """One pass: fetch, select unaudited, audit, persist verdicts.

    With prune=True, retire all vetoed hypotheses still on the graph
    (state vetoes plus Audit-challenged nodes) in one POST /retire
    call. No model resolution, no LLM calls, no re-audit.
    """
    try:
        data = _get_json(graph_url.rstrip("/") + "/graph")
    except Exception as e:
        return {"ok": False, "reason": f"graph unreachable: {e}"}
    state = load_state(state_path)
    elements = data.get("elements", [])
    if prune:
        vetoed = select_vetoed(elements, state)
        if not vetoed:
            return {"ok": True, "mode": "prune", "audited": 0,
                    "kept": 0, "vetoed": 0, "posted": 0,
                    "pruned": 0, "llm_calls": 0, "retired": []}
        if dry_run:
            return {"ok": True, "mode": "prune", "audited": 0,
                    "kept": 0, "vetoed": len(vetoed), "posted": 0,
                    "pruned": 0, "llm_calls": 0, "retired": [],
                    "vetoed_ids": vetoed}
        retire = retire_fn or _post_json
        try:
            resp = retire(graph_url.rstrip("/") + "/retire",
                          {"node_ids": vetoed})
        except Exception as e:
            return {"ok": False,
                    "reason": f"prune failed: {e}"}
        retired = resp.get("retired", [])
        removed = [v.get("entity_id") for v in retired
                   if v.get("removed")]
        for hid in removed:
            log(f"pruned {hid}")
        return {"ok": True, "mode": "prune", "audited": 0,
                "kept": 0, "vetoed": len(vetoed), "posted": 0,
                "pruned": len(removed), "llm_calls": 0,
                "retired": removed}
    explicit_none = model is None or model == "None"
    if explicit_none:
        model_obj = None
    else:
        model_obj = resolve_model(model, role="auditor")
        # Do not let a missing key masquerade as a real "keep" verdict.
        if model_obj is None:
            return {"ok": False,
                    "reason": "no model key configured "
                              "(use --model None to keep without LLM)"}
    candidates = select_candidates(elements, state, max_audits)
    if not candidates:
        return {"ok": True, "audited": 0, "kept": 0, "vetoed": 0,
                "posted": 0, "pruned": 0, "llm_calls": 0}
    res = audit_pass(graph_url, candidates, model_obj, dry_run, post_fn,
                     False, retire_fn)
    if not dry_run:
        # Transient verdicts (model error, failed challenge post) are
        # deliberately left out of state so the next pass retries them.
        recordable = {hid: v for hid, v in res["verdicts"].items()
                      if not v.get("transient")}
        state.update(recordable)
        save_state(state_path, state)
        res["recorded"] = len(recordable)
    res["audited"] = len(candidates)
    res["ok"] = True
    return res


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--model", default="auto",
              help="LLM model spec (auto|None|model-id); None = keep all")
@click.option("--max-audits", type=int, default=20,
              help="hypotheses per pass, 0 = no cap")
@click.option("--state", "state_path", default="data_local/audit_state.json")
@click.option("--loop", is_flag=True, default=False)
@click.option("--max-cycles", type=int, default=0,
              help="stop after N loop cycles (0 = run forever)")
@click.option("--interval-min", type=float, default=60)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--prune", is_flag=True, default=False,
              help="retire ALL vetoed hypotheses via POST /retire, "
                   "no re-audit")
@click.option("--langsmith-project", default=None)
def main(graph_url, model, max_audits, state_path, loop,
         max_cycles, interval_min, dry_run, prune,
         langsmith_project) -> None:
    from services.kalshi_auth import load_dotenv  # noqa: E402
    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    load_dotenv()
    ls = setup_langsmith(project=langsmith_project)
    log(f"langsmith={'on' if ls['enabled'] else 'off'} "
        f"project={ls['project']}")
    cycle = 0
    while True:
        with squad_span("audit-loop-cycle", {"cycle": cycle}) as span:
            try:
                summary = run_once(graph_url, model, max_audits,
                                   dry_run, state_path,
                                   prune=prune)
            except Exception as e:  # keep the loop alive; record and retry
                summary = {"ok": False,
                           "reason": f"{type(e).__name__}: {e}"}
            span.outputs = summary
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        print(f"[audit-loop] {stamp} cycle={cycle} {summary}", flush=True)
        if not loop:
            sys.exit(0 if summary.get("ok") else 1)
        cycle += 1
        if max_cycles and cycle >= max_cycles:
            sys.exit(0)
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
