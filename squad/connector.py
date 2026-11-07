"""Connector: follow-up dig dispatch (topic -> news -> topics loop).

Derived edges are read-time only (see graph.derive), so there is no
write lane: followups() wraps graph.followup_tasks so the news agent
digs related topics after each cycle.
"""

from dataclasses import dataclass

from graph import followup_tasks


@dataclass
class Connector:
    """Dispatch helper: investigate tasks for related events."""

    name: str = "connector"

    def followups(self, store, event_id: str) -> list[dict]:
        """Investigate tasks for events sharing subjects with event_id."""
        eid = event_id if event_id.startswith("kalshi:") else f"kalshi:{event_id}"
        return followup_tasks(store, eid)


ConnectorAgent = Connector
