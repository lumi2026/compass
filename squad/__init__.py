"""Layer 3 contracts: agent squad orchestrator.

Scout / Investigator / Connector propose in fan-out; Skeptic vetoes;
only approved proposals reach the Graph Update API. Agents speak the
Proposal contract, so models swap without rewiring (LangChain later).
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Proposal:
    agent: str
    action: str  # e.g. "link", "hypothesis", "flag"
    entity_id: str
    detail: str = ""
    confidence: float = 0.5


@dataclass(frozen=True)
class Verdict:
    approved: bool
    reason: str = ""


class Agent(Protocol):
    """Contract every squad agent must satisfy."""

    name: str

    def run(self, task: dict | str, context: dict) -> list[Proposal]:
        ...


class Skeptic:
    """Gate: vetoes low-confidence or empty proposals."""

    name = "skeptic"

    def __init__(self, min_confidence: float = 0.3) -> None:
        self.min_confidence = min_confidence

    def review(self, proposals: list[Proposal]) -> tuple[list[Proposal], list[Verdict]]:
        approved: list[Proposal] = []
        verdicts: list[Verdict] = []
        for p in proposals:
            ok = bool(p.entity_id and p.action) and p.confidence >= self.min_confidence
            verdicts.append(
                Verdict(approved=ok, reason="" if ok else "vetoed: weak proposal")
            )
            if ok:
                approved.append(p)
        return approved, verdicts


class Orchestrator:
    """Contract: fan-out to agents, gate through skeptic."""

    def __init__(self, agents: list[Agent], skeptic: Skeptic | None = None) -> None:
        self.agents = agents
        self.skeptic = skeptic or Skeptic()

    def investigate(self, task: dict | str, context: dict | None = None) -> list[Proposal]:
        ctx = context or {}
        proposals: list[Proposal] = []
        for agent in self.agents:
            proposals.extend(agent.run(task, ctx))
        approved, _ = self.skeptic.review(proposals)
        return approved
