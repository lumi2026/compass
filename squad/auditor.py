"""Auditor: third review agent. Vetoes spurious interconnects.

Lane: ROLE_ACTIONS["auditor"] == ("add-node", "add-edge").
New links are audited pre-commit (veto = skip the pair's proposals).
Bad links that already committed are flagged, not deleted: an Audit
node plus a `challenges` edge, both reversible and auditable. Hard
removal (tombstones) is a later migration.

Fail-open like the rest of the squad: no model, or an unparseable
reply, keeps the link. Vetoes require an explicit NO.
"""

from dataclasses import dataclass


@dataclass
class AuditResult:
    keep: bool
    reason: str = ""
    confidence: float = 0.5


def audit_pair(topic_a: str, title_a: str, topic_b: str, title_b: str,
               evidence_titles: list[str], mode: str,
               model=None) -> AuditResult:
    """Judge one linked pair against its shared evidence.

    Veto ONLY when the shared articles are substantively about a
    different country, market, or entity than the link claims (e.g. a
    Mexico GDP article linking two US GDP markets). Same office or
    event type across countries (two prime-minister races sharing
    prime-minister coverage) is a genuine link: keep it.
    """
    if model is None:
        return AuditResult(True, "no-model: keep (fail-open)", 0.5)
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        msg = model.invoke([
            SystemMessage(
                "You audit topic links in a prediction-market knowledge "
                "graph. Two market topics were linked because the same "
                "news articles matched both. Reply NO only if the shared "
                "articles are substantively about a different country, "
                "market, or entity than the link claims. Reply YES if the "
                "articles genuinely concern both topics, the same office "
                "or event type, or the same unfolding story, even across "
                "countries. Then a one-line reason, then a confidence "
                "number 0-1 on its own line."),
            HumanMessage(
                f"Topic A: {topic_a} ({title_a})\n"
                f"Topic B: {topic_b} ({title_b})\n"
                f"Mode: {mode}\n"
                f"Shared articles: {'; '.join(evidence_titles[:5]) or '(none)'}"),
        ])
        lines = str(msg.content).strip().splitlines()
        verdict = lines[0].strip().upper() if lines else ""
        reason = lines[1].strip()[:300] if len(lines) > 1 else ""
        conf = 0.6
        for line in lines:
            try:
                conf = max(0.0, min(0.9, float(line.strip())))
                break
            except ValueError:
                continue
        if verdict.startswith("NO"):
            return AuditResult(False, reason or "spurious link", conf)
        return AuditResult(True, reason or "genuine link", conf)
    except Exception:
        return AuditResult(True, "llm-error: keep (fail-open)", 0.5)
