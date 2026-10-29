"""MVP offline test: two events sharing a subject link up; viewer renders."""

from graph import GraphStore, GraphUpdateAPI, proposals_to_commit
from squad import Skeptic
from squad.connector import Connector
from squad.investigator import Investigator
from squad.news_agent import NewsAgent
from squad.pipeline import build_investigator_context
from squad.scout import Scout
from workspace import to_cytoscape
from workspace.viewer import render_page


class FakeMarkets:
    def __init__(self, ticker, title):
        self.ticker = ticker
        self.title = title


def _commit_all(api, props):
    for p in Skeptic().review(props)[0]:
        r = proposals_to_commit(api, p.agent, p.action, p.entity_id, p.detail)
        assert r.committed, r.reason


def test_mvp_two_events_share_subject_link():
    api = GraphUpdateAPI(GraphStore())
    tasks = [
        {"event_ticker": "KXEVA-1", "terms": ["spacex"]},
        {"event_ticker": "KXEVB-2", "terms": ["spacex", "nato"]},
    ]
    kept_sets = [
        [{"url": "http://mvp.test/a1", "title": "A1",
          "subjects": ["spacex"], "snapshot": "ts", "seendate": "ts"}],
        [{"url": "http://mvp.test/b1", "title": "B1",
          "subjects": ["spacex"], "snapshot": "ts", "seendate": "ts"}],
    ]
    scout = Scout(markets_fn=lambda e: [FakeMarkets(f"{e}-M", f"{e} title")])
    for task, kept in zip(tasks, kept_sets):
        markets = scout.markets_fn(task["event_ticker"])
        _commit_all(api, scout.run(task, {"markets": markets}))
        _commit_all(api, NewsAgent().graph_proposals(task, kept))
        _commit_all(api, Investigator().run(
            task, build_investigator_context(kept=kept, markets=markets)))
    (intent,) = Connector().run(tasks[0])
    r = proposals_to_commit(api, intent.agent, intent.action,
                            intent.entity_id, intent.detail)
    assert r.committed and "derived 2" in r.reason

    payload = to_cytoscape(list(api.store.nodes.values()), api.store.edges)
    assert len(payload["elements"]) > 0
    page = render_page(payload, title="MVP test")
    assert "cytoscape" in page
    assert "kalshi:KXEVA-1" in page and "kalshi:KXEVB-2" in page


def test_viewer_empty_graph():
    page = render_page({"elements": []}, title="empty")
    assert 'id="counts"' in page and "cytoscape" in page
