"""Layer 2 contracts: event processing.

Pure functions: normalize incoming items, detect meaningful changes,
emit investigation triggers. No I/O here; sources fetch, graph stores.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChangeEvent:
    entity_id: str
    kind: str  # e.g. "new_event", "new_article", "price_shift"
    summary: str


def detect_new_items(
    seen_ids: set[str], items: list, kind: str = "new_item"
) -> list[ChangeEvent]:
    """Emit one ChangeEvent per item whose id has not been seen."""
    changes: list[ChangeEvent] = []
    for item in items:
        eid = f"{item.source}:{item.external_id}"
        if eid not in seen_ids:
            changes.append(
                ChangeEvent(entity_id=eid, kind=kind, summary=item.title[:120])
            )
    return changes


def investigation_triggers(changes: list[ChangeEvent], threshold: int = 1) -> bool:
    """Contract: squad runs when change count reaches threshold."""
    return len(changes) >= threshold
