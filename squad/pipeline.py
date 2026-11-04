"""Squad pipeline: wire scout/news outputs into investigator context.

Orchestrator order: scout + news first, feed approved outputs into
investigator, then connector. Only graph_proposals() commits —
NewsAgent.run news_summary stays a log-only digest.

Also homes the change-detection helpers (absorbed from the old
`processing` layer, whose only consumer was this module): pure
functions, no I/O here.
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


def build_investigator_context(news_proposal=None, graph_props=None,
                               kept=None, markets=None,
                               lead_question=None, hypothesis_type=None,
                               mode=None) -> dict:
    """Context for Investigator.run from prior squad outputs."""
    ctx: dict = {}
    if news_proposal is not None:
        ctx["digest"] = getattr(news_proposal, "detail", str(news_proposal))
    if graph_props is not None:
        ctx["graph"] = [p.__dict__ if hasattr(p, "__dict__") else dict(p)
                        for p in graph_props]
    if kept is not None:
        ctx["kept"] = kept
    if markets is not None:
        ctx["markets"] = markets
    if lead_question:
        ctx["lead_question"] = lead_question
    if hypothesis_type:
        ctx["hypothesis_type"] = hypothesis_type
    if mode:
        ctx["mode"] = mode
    triggers = investigation_triggers([])
    ctx["triggers"] = triggers
    return ctx


def change_triggered(seen_ids: set[str], items: list, kind: str = "new_item") -> bool:
    """Threshold gate for re-investigation (change helpers above)."""
    return investigation_triggers(detect_new_items(seen_ids, items, kind=kind))
