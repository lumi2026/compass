"""Squad pipeline: wire scout/news outputs into investigator context.

Orchestrator order: scout + news first, feed approved outputs into
investigator, then connector. Only graph_proposals() commits —
NewsAgent.run news_summary stays a log-only digest.
"""

from processing import detect_new_items, investigation_triggers


def build_investigator_context(news_proposal=None, graph_props=None,
                               kept=None, markets=None,
                               lead_question=None) -> dict:
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
    triggers = investigation_triggers([])
    ctx["triggers"] = triggers
    return ctx


def change_triggered(seen_ids: set[str], items: list, kind: str = "new_item") -> bool:
    """Threshold gate for re-investigation (processing helpers)."""
    return investigation_triggers(detect_new_items(seen_ids, items, kind=kind))
