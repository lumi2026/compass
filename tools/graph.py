"""Graph API tools (inspect state, propose through the gate)."""

import json
import urllib.request


def _get(base: str, path: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"User-Agent": "compass/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post(base: str, path: str, payload: dict,
          timeout: int = 120) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _elements(base: str) -> list:
    return _get(base, "/graph").get("elements", [])


def graph_tools(base_url: str) -> dict:
    """{tool_name: {description, func}} bound to one graph API."""

    def graph_summary() -> dict:
        """Revision + node/edge counts (cheap liveness check)."""
        try:
            data = _get(base_url, "/health")
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "revision": data.get("revision"),
                "nodes": data.get("nodes"), "edges": data.get("edges")}

    def list_hypotheses() -> dict:
        """Committed hypotheses with endpoints, evidence, challenges."""
        try:
            elements = _elements(base_url)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        nodes = {}
        for el in elements:
            d = el.get("data", {})
            if "source" not in d:
                nodes[d.get("id")] = d
        supports: dict[str, list[str]] = {}
        challenged: set[str] = set()
        for el in elements:
            d = el.get("data", {})
            if d.get("label") == "supports":
                supports.setdefault(d.get("source", ""), []).append(
                    d.get("target", ""))
            elif d.get("label") == "challenges":
                challenged.add(d.get("target", ""))
                src = nodes.get(d.get("source", ""), {})
                if src.get("props", {}).get("target"):
                    challenged.add(src["props"]["target"])
        out = []
        for hid, n in nodes.items():
            if n.get("kind") != "Hypothesis":
                continue
            ends = supports.get(hid, [])
            out.append({
                "id": hid, "title": n.get("label", hid),
                "events": [t for t in ends
                           if nodes.get(t, {}).get("kind") == "Event"],
                "articles": len([t for t in ends
                                 if nodes.get(t, {}).get("kind") == "Article"]),
                "challenged": hid in challenged})
        out.sort(key=lambda h: h["id"])
        return {"ok": True, "hypotheses": out}

    def post_proposals(proposals_json: str) -> dict:
        """Commit gated proposals (JSON list of agent/action/entity_id/
        detail/confidence). Writes go through Skeptic + role lanes."""
        try:
            proposals = json.loads(proposals_json)
            if not isinstance(proposals, list):
                raise ValueError("top-level JSON must be a list")
            data = _post(base_url, "/proposals", {"proposals": proposals})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        verdicts = data.get("verdicts", [])
        return {"ok": True, "revision": data.get("revision"),
                "committed": sum(1 for v in verdicts
                                 if v.get("committed")),
                "rejected": sum(1 for v in verdicts
                                if not v.get("committed"))}

    def post_finding(text: str, extra_json: str = "{}") -> dict:
        """Append an ops note to the findings log (JSON object string)."""
        try:
            extra = json.loads(extra_json or "{}")
            if not isinstance(extra, dict):
                raise ValueError("extra_json must be an object")
            data = _post(base_url, "/findings",
                         {"finding": {"note": text, **extra}})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "posted": data.get("posted", True)}

    def trigger_derive() -> dict:
        """Recompute Connector derivations (shared-subject, has-market)."""
        try:
            data = _post(base_url, "/proposals", {"proposals": [
                {"agent": "connector", "action": "derive-links",
                 "entity_id": "", "detail": "", "confidence": 0.9}]})
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "derived_fresh": data.get("derived_fresh", 0),
                "revision": data.get("revision")}

    return {
        "graph_summary": {
            "description": "Revision + node/edge counts.",
            "func": graph_summary},
        "graph_list_hypotheses": {
            "description": "Hypotheses with endpoints, evidence counts, "
                           "challenge flags.",
            "func": list_hypotheses},
        "graph_post_proposals": {
            "description": "Commit gated proposals (JSON list). Writes go "
                           "through Skeptic + role lanes.",
            "func": post_proposals},
        "graph_post_finding": {
            "description": "Append an ops note to the findings log.",
            "func": post_finding},
        "graph_trigger_derive": {
            "description": "Recompute Connector derivations.",
            "func": trigger_derive},
    }
