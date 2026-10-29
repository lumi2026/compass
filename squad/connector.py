"""Connector: derive event-event links + dispatch follow-up digs.

Lane: ROLE_ACTIONS["connector"] == ("derive-links",). Derived edges
are never hand-written via proposals_to_commit — run() emits a
derive-links intent, and apply() records them via
graph.refresh_derived_links (idempotent). followups() wraps
graph.followup_tasks so the topic -> news -> topics loop closes.
"""

from dataclasses import dataclass
import json

from graph import followup_tasks, refresh_derived_links
from squad import Proposal


def _event_id(task: dict | str, context: dict | None = None) -> str:
    if isinstance(task, str):
        t = task
    elif isinstance(task, dict):
        t = task.get("event_id") or task.get("event_ticker", "")
    else:
        t = ""
    if context and not t:
        t = context.get("event_id", "") or context.get("event_ticker", "")
    if not t:
        return ""
    return t if t.startswith("kalshi:") else f"kalshi:{t}"


@dataclass
class Connector:
    """Link agent: intent proposals + derive/dispatch helpers."""

    name: str = "connector"

    def run(self, task: dict | str, context: dict | None = None) -> list[Proposal]:
        eid = _event_id(task, context)
        if not eid:
            return []
        return [Proposal(
            agent=self.name, action="derive-links", entity_id=eid,
            detail=json.dumps({"event_id": eid}),
            confidence=0.8,
        )]

    def apply(self, api) -> int:
        """Record all derived links. Returns newly recorded count."""
        return refresh_derived_links(api)

    def followups(self, store, event_id: str) -> list[dict]:
        """Investigate tasks for events sharing subjects with event_id."""
        eid = event_id if event_id.startswith("kalshi:") else f"kalshi:{event_id}"
        return followup_tasks(store, eid)


ConnectorAgent = Connector
