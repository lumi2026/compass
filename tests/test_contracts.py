"""Deterministic contract tests. No network: fixtures only."""

from graph import CommitResult, GraphStore, GraphUpdateAPI, Node, Edge
from processing import ChangeEvent, detect_new_items, investigation_triggers
from sources.base import MarketEvent
from squad import Orchestrator, Proposal, Skeptic
from workspace import MAX_VISIBLE_NODES, feedback_to_task, to_cytoscape


class EchoAgent:
    name = "echo"

    def __init__(self, proposals):
        self._proposals = proposals

    def run(self, task, context):
        return self._proposals


def test_source_contract_shape():
    item = MarketEvent(source="kalshi", external_id="E1", title="Test event")
    assert item.source and item.external_id and item.title


def test_processing_detects_only_new_items():
    items = [
        MarketEvent(source="kalshi", external_id="E1", title="one"),
        MarketEvent(source="kalshi", external_id="E2", title="two"),
    ]
    changes = detect_new_items({"kalshi:E1"}, items, kind="new_event")
    assert [c.entity_id for c in changes] == ["kalshi:E2"]
    assert investigation_triggers(changes) is True
    assert investigation_triggers([]) is False


def test_skeptic_vetoes_weak_proposals():
    good = Proposal(agent="scout", action="link", entity_id="kalshi:E1", confidence=0.9)
    weak = Proposal(agent="scout", action="", entity_id="", confidence=0.0)
    approved, verdicts = Skeptic(min_confidence=0.3).review([good, weak])
    assert approved == [good]
    assert [v.approved for v in verdicts] == [True, False]


def test_orchestrator_gates_through_skeptic():
    good = Proposal(agent="scout", action="link", entity_id="kalshi:E1", confidence=0.8)
    bad = Proposal(agent="scout", action="", entity_id="", confidence=0.0)
    orch = Orchestrator(agents=[EchoAgent([good, bad])])
    assert orch.investigate("check E1") == [good]


def test_graph_update_api_idempotent_and_validating():
    api = GraphUpdateAPI(GraphStore())
    node = Node(id="kalshi:E1", kind="Event", title="Test")
    first = api.commit(node)
    second = api.commit(node)
    assert first.committed and second.committed
    assert second.reason.startswith("duplicate")
    bad = api.commit(Node(id="", kind="", title=""))
    assert bad.committed is False
    api.record(Edge(src="kalshi:E1", dst="gdelt:a1", rel="relates-to", at="t"))
    assert len(api.store.edges) == 1


def test_workspace_contracts():
    nodes = [Node(id=f"n{i}", kind="Event", title=f"t{i}") for i in range(3)]
    payload = to_cytoscape(nodes, [])
    assert len(payload["elements"]) == 3
    assert MAX_VISIBLE_NODES == 200
    task = feedback_to_task("challenge", "kalshi:E1", "why?")
    assert task.kind == "challenge"
    try:
        feedback_to_task("nope", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown feedback kind")
