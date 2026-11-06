"""Offline event-graph POC: seed markets + fixture articles -> linked events.

No network. Loads data_local/seed.json (7 Kalshi markets in 3 event families),
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
    materialize_derived_links, materialize_derived_nodes,
    proposals_to_commit, to_elements,
)

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

    seed = json.load(open("data_local/seed.json"))["tickers"]
    api = GraphUpdateAPI(GraphStore())

    # Scout lane: events with folded markets in props.
    events: dict[str, str] = {}
    for mt, m in seed.items():
        ev = event_of(mt)
        events.setdefault(ev, m["last"]["title"])
    markets: dict[str, dict] = {}
    for mt, m in seed.items():
        markets.setdefault(event_of(mt), {})[mt] = {
            "title": f"{m['last']['title']} [{mt}]",
            "event_ticker": event_of(mt),
        }
    for ev, title in events.items():
        print(proposals_to_commit(
            api, "scout", "add-node", f"kalshi:{ev}",
            json.dumps({"kind": "Event", "title": title,
                        "props": {"markets": markets.get(ev, {})}})).reason)

    # News-agent lane: articles carry folded subjects + about edges.
    for a in ARTICLES:
        aid = article_id(a["url"])
        proposals_to_commit(api, "news-agent", "add-node", aid,
                            json.dumps({"kind": "Article", "title": a["title"],
                                        "props": {"subjects": a["subjects"]}}))
        for ev in a["about"]:
            proposals_to_commit(api, "news-agent", "add-edge", "",
                                json.dumps({"src": aid, "dst": f"kalshi:{ev}",
                                            "rel": "about"}))

    # Connector lane: derived links are materialized on read only.
    derived = materialize_derived_links(api.store)
    print(f"[connector] read-time derived links: {len(derived)}")

    # Connector dispatch: the NATO race is "new" — who else should we dig?
    followups = followup_tasks(api.store, "kalshi:KXNEXTNATOSECGEN-99")
    print("followups:", followups)

    pairs = sorted({(e.src, e.dst) for e in derived
                    if e.rel == "shares-subject-with"})
    print(f"nodes={len(api.store.nodes)} "
          f"persisted edges={len(api.store.edges)}")
    print("event links:", pairs)
    seen = {(e.src, e.dst, e.rel) for e in api.store.edges}
    merged = list(api.store.edges) + [
        e for e in derived if (e.src, e.dst, e.rel) not in seen]
    nodes = list(api.store.nodes.values())
    nodes += [n for n in materialize_derived_nodes(api.store)
              if n.id not in api.store.nodes]
    payload = to_elements(nodes, merged)
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
