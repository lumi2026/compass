"""Auditor: third review agent. Vetoes spurious interconnects.

Lane: ROLE_ACTIONS["auditor"] == ("add-node", "add-edge").
New links are audited pre-commit (veto = skip the pair's proposals).
Bad links that already committed are flagged (an Audit node plus a
`challenges` edge, both reversible and auditable) and retired via
POST /retire in the audit loop.

Fail-open like the rest of the squad: no model, or an unparseable
reply, keeps the link. Vetoes require an explicit NO.
"""

from dataclasses import dataclass

from .taxonomy import normalize_hypothesis_type


@dataclass
class AuditResult:
    keep: bool
    reason: str = ""
    confidence: float = 0.5


@dataclass
class DeliberationResult:
    keep: bool
    title: str = ""
    reason: str = ""
    confidence: float = 0.5
    turns_used: int = 0


def _is_veto(lines: list[str]) -> bool:
    return bool(lines) and lines[0].strip().upper().startswith("NO")


def _parse_conf(lines: list[str], default: float = 0.6) -> float:
    for line in lines:
        try:
            return max(0.0, min(0.9, float(line.strip())))
        except ValueError:
            continue
    return default


def _parse_reason(lines: list[str], default: str = "") -> str:
    return lines[1].strip()[:300] if len(lines) > 1 else default


# Taxonomy (HYPOTHESIS_TYPES, mode mapping, normalize) is shared with
# the squad and the view via shared/hypothesis_taxonomy.json.

# What each claimed type must show in the evidence. The audit vetoes
# when the evidence does not support the CLAIMED type, not just when
# the articles are off-topic.
_TYPE_CHECKS = {
    "co_mention": "the same articles must genuinely mention or cover "
                  "both topics (one shared story touching both)",
    "co_location": "the evidence must show a real shared place "
                   "(same city, country, venue, or route)",
    "shared_context": "the topics must share a genuine subject, "
                      "office, event type, or unfolding story, even "
                      "across countries",
    "co_outcome": "the topics' outcomes must plausibly move together "
                  "(linked markets, odds, or consequences)",
}


def _type_criterion(mode: str) -> tuple[str | None, str, str]:
    """System/human prompt fragments for the claimed link type.

    Returns (htype, system_criterion, human_claim). Unknown modes fall
    back to the generic substantive-mismatch test (fail-open).
    """
    htype = normalize_hypothesis_type(mode)
    check = _TYPE_CHECKS.get(htype) if htype else None
    if check:
        return (htype,
                f"The claimed link type is {htype}: {check}. Reply NO "
                "if the evidence does not support that type of link.",
                f"\nClaimed type: {htype} ({check})")
    return (None,
            "Reply NO only if the shared articles are substantively "
            "about a different country, market, or entity than the "
            "link claims.",
            "")


def audit_pair(topic_a: str, title_a: str, topic_b: str, title_b: str,
               evidence_titles: list[str], mode: str,
               model=None) -> AuditResult:
    """Judge one linked pair against its shared evidence.

    The mode (detection mode or stored closed type) selects the test:
    the evidence must support the CLAIMED type (e.g. co_location
    needs a real shared place, co_outcome needs linked outcomes).
    Unknown modes fall back to the generic substantive-mismatch test,
    fail-open rather than vetoed.

    Veto ONLY when the evidence does not support the claimed link
    (e.g. a Mexico GDP article linking two US GDP markets). Same
    office or event type across countries (two prime-minister races
    sharing prime-minister coverage) is a genuine shared_context
    link: keep it.
    """
    if model is None:
        return AuditResult(True, "no-model: keep (fail-open)", 0.5)
    htype, criterion, claim = _type_criterion(mode)
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        msg = model.invoke([
            SystemMessage(
                "You audit topic links in a prediction-market knowledge "
                "graph. Two market topics were linked because the same "
                "news articles matched both. " + criterion + " Reply YES "
                "if the articles genuinely support the claimed link, "
                "even across countries. Then a one-line reason, then a "
                "confidence number 0-1 on its own line."),
            HumanMessage(
                f"Topic A: {topic_a} ({title_a})\n"
                f"Topic B: {topic_b} ({title_b})\n"
                f"Mode: {mode}{claim}\n"
                f"Shared articles: {'; '.join(evidence_titles[:5]) or '(none)'}"),
        ])
        lines = str(msg.content).strip().splitlines()
        conf = _parse_conf(lines)
        if _is_veto(lines):
            return AuditResult(False,
                               _parse_reason(lines, "spurious link"), conf)
        return AuditResult(True,
                           _parse_reason(lines, "genuine link"), conf)
    except Exception:
        return AuditResult(True, "llm-error: keep (fail-open)", 0.5)


def deliberate_pair(topic_a: str, title_a: str, topic_b: str, title_b: str,
                    evidence_titles: list[str], mode: str,
                    model=None) -> DeliberationResult:
    """Multi-turn alternative to propose-then-audit for one topic pair.

    What it is for: testing whether letting the model argue with
    itself in one shared session produces better links than the
    default pipeline (hypothesis_title proposes cold, audit_pair
    vetoes cold). The shared context lets turn 2 quote and challenge
    turn 1's own claims instead of re-reading cold evidence.

    Turns: 1 proposes the link; 2 audits that proposal in-context;
    3 salvages a narrower true claim or confirms the drop.
    Fail-open throughout (errors and no-model keep the pair).

    Cost: up to 3 model calls per pair vs 2 for single-shot
    (propose + audit). Measured 2026-10-31 on 11 labeled fixtures:
    identical verdicts both arms (precision=recall=1.00), 32 calls
    vs 22 — no gain, so single-shot stays the default and this
    remains opt-in (news_loop --deliberate) for future tests on
    harder near-miss fixtures.
    """
    if model is None:
        head = "; ".join(evidence_titles[:2]) or "no evidence"
        return DeliberationResult(True, f"shared coverage: {head}",
                                  "no-model: keep (fail-open)", 0.5, 0)
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    htype, criterion, claim = _type_criterion(mode)
    ev = "; ".join(evidence_titles[:5]) or "(none)"
    context = (f"Topic A: {topic_a} ({title_a})\n"
               f"Topic B: {topic_b} ({title_b})\n"
               f"Mode: {mode}{claim}\nShared articles: {ev}")
    turns = 0
    try:
        msgs = [SystemMessage(
                    "You are a prediction-market analyst linking topics "
                    "through world news. Propose one sentence stating how "
                    "the two topics are related, grounded in the shared "
                    "articles."
                    + (f" The claimed link type is {htype}."
                       if htype else "")),
                HumanMessage(context + "\nPropose the link in one sentence.")]
        t1 = model.invoke(msgs)
        turns = 1
        first = str(t1.content).strip().splitlines()
        title = first[0].strip()[:300] if first else ""
        msgs += [AIMessage(str(t1.content)),
                 HumanMessage(
                     "Now audit your own proposal against the same "
                     "evidence. " + criterion + " Reply YES if it holds. "
                     "Then a one-line reason, then a confidence number "
                     "0-1 on its own line.")]
        t2 = model.invoke(msgs)
        turns = 2
        lines = str(t2.content).strip().splitlines()
        if not _is_veto(lines):
            return DeliberationResult(
                True, title, _parse_reason(lines, "genuine link"),
                _parse_conf(lines), turns)
        msgs += [AIMessage(str(t2.content)),
                 HumanMessage(
                     "You vetoed your own link. Either state a narrower "
                     "claim that IS supported by the evidence (one "
                     "sentence), or reply DROP. Then a one-line reason.")]
        t3 = model.invoke(msgs)
        turns = 3
        l3 = str(t3.content).strip().splitlines()
        if l3 and l3[0].strip().upper() == "DROP":
            return DeliberationResult(
                False, title, _parse_reason(l3, "dropped on review"),
                _parse_conf(l3), turns)
        revised = l3[0].strip()[:300] if l3 else title
        return DeliberationResult(
            True, revised, _parse_reason(l3, "narrowed on review"),
            _parse_conf(l3), turns)
    except Exception:
        return DeliberationResult(True, "", "llm-error: keep (fail-open)",
                                  0.5, turns)
