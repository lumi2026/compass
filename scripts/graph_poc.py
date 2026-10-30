"""Offline event-graph POC: seed markets + fixture articles -> linked events.

No network. Loads data/seed.json (7 Kalshi markets in 3 event families),
adds 3 hand-written fixture articles with subjects, writes nodes/edges
through each agent lane (scout / news-agent / connector) via the gated
GraphUpdateAPI, derives event-to-event links, and checks the workspace
view. Fails loudly when the expected link is missing.

Run:  python3 scripts/graph_poc.py [--out /tmp/graph_poc.json]
"""

import json
import sys

import click

sys.path.insert(0, ".")

from graph import (  # noqa: E402
    GraphStore, GraphUpdateAPI, article_id, followup_tasks,
    refresh_derived_links, proposals_to_commit,
)
from workspace import to_cytoscape  # noqa: E402

# Fixture articles: A1 ties Elon/Mars to SpaceX; A2 ties the same subject
# to the NATO race; A3 is pope-only (control: must stay unlinked).
ARTICLES = [
    {"url": "https://poc.test/starship-mars-window",
     "title": "Starship Mars window slips as SpaceX reworks heat shield",
     "subjects": ["spacex", "mars"],
     "about": ["KXELONMARS-99"]},
    {"url": "https://poc.test/nato-space-policy",
     "title": "NATO hopefuls pitch shared space lift, cite SpaceX cadence",
     "subjects": ["spacex", "nato"],
     "about": ["KXNEXTNATOSECGEN-99"]},
    {"url": "https://poc.test/conclave-watch",
     "title": "Conclave watch: cardinals begin pre-vote meetings",
     "subjects": ["pope"],
     "about": ["KXNEWPOPE-70"]},
]


def event_of(market_ticker: str) -> str:
    """Seed-specific heuristic: strip a trailing -LETTERS candidate suffix."""
    head, _, tail = market_ticker.rpartition("-")
    if tail.isalpha() and len(tail) >= 2:
        return head
    return market_ticker


@click.command()
@click.option("--out", default="/tmp/graph_poc.json")
def main(out) -> None:

    seed = json.load(open("data/seed.json"))["tickers"]
    api = GraphUpdateAPI(GraphStore())

    # Scout lane: events + markets.
    events: dict[str, str] = {}
    for mt, m in seed.items():
        ev = event_of(mt)
        events.setdefault(ev, m["last"]["title"])
    for ev, title in events.items():
        print(proposals_to_commit(api, "scout", "add-node", f"kalshi:{ev}",
                                  json.dumps({"kind": "Event", "title": title})).reason)
    for mt, m in seed.items():
        print(proposals_to_commit(
            api, "scout", "add-node", f"market:{mt}",
            json.dumps({"kind": "Market",
                        "title": f"{m['last']['title']} [{mt}]"})).reason)
        print(proposals_to_commit(
            api, "investigator", "add-edge", "",
            json.dumps({"src": f"market:{mt}", "dst": f"kalshi:{event_of(mt)}",
                        "rel": "relates-to"})).reason)

    # News-agent lane: articles, subjects, mentions + about edges.
    for a in ARTICLES:
        aid = article_id(a["url"])
        proposals_to_commit(api, "news-agent", "add-node", aid,
                            json.dumps({"kind": "Article", "title": a["title"]}))
        for s in a["subjects"]:
            proposals_to_commit(api, "news-agent", "add-node", f"subject:{s}",
                                json.dumps({"kind": "Subject", "title": s}))
            proposals_to_commit(api, "news-agent", "add-edge", "",
                                json.dumps({"src": aid, "dst": f"subject:{s}",
                                            "rel": "mentions"}))
        for ev in a["about"]:
            proposals_to_commit(api, "news-agent", "add-edge", "",
                                json.dumps({"src": aid, "dst": f"kalshi:{ev}",
                                            "rel": "about"}))

    # Connector lane: refresh derived links (idempotent re-runs OK).
    fresh = refresh_derived_links(api)
    print(f"[connector] fresh links: {fresh}")
    assert refresh_derived_links(api) == 0, "recompute must be idempotent"

    # Connector dispatch: the NATO race is "new" — who else should we dig?
    followups = followup_tasks(api.store, "kalshi:KXNEXTNATOSECGEN-99")
    print("followups:", followups)

    pairs = sorted({(e.src, e.dst) for e in api.store.edges
                    if e.rel == "shares-subject-with"})
    print(f"nodes={len(api.store.nodes)} edges={len(api.store.edges)}")
    print("event links:", pairs)
    payload = to_cytoscape(list(api.store.nodes.values()), api.store.edges)
    print(f"workspace elements={len(payload['elements'])}")
    api.save(out)
    print(f"[poc] wrote {out}")
    reloaded = GraphUpdateAPI.load(out)
    assert len(reloaded.store.nodes) == len(api.store.nodes)
    assert len(reloaded.store.edges) == len(api.store.edges)
    print("[poc] save/load roundtrip OK")

    want = ("kalshi:KXELONMARS-99", "kalshi:KXNEXTNATOSECGEN-99")
    if want not in pairs:
        print("[poc] FAIL: expected Elon<->NATO link missing")
        sys.exit(1)
    if any("KXNEWPOPE-70" in (s, d) for s, d in pairs):
        print("[poc] FAIL: pope event should stay unlinked")
        sys.exit(1)
    print("[poc] OK: link via subject:spacex, control isolated")
    sys.exit(0)


if __name__ == "__main__":
    main()
