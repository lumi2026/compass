"""Graph derivation: Connector-only links, dispatch, and time travel.

Pure functions over a GraphStore, except refresh_derived_links which
records through the gate (idempotent: reinforcements don't count as new).
"""

from typing import TYPE_CHECKING

from .store import Edge, GraphStore, Node

if TYPE_CHECKING:  # avoid gate<->derive import cycle at runtime
    from .gate import GraphUpdateAPI


def _subject_events(store: GraphStore) -> dict[str, set[str]]:
    """subject node id -> events, via article-mediated mentions + about."""
    mentions: dict[str, set[str]] = {}  # subject -> articles
    about: dict[str, set[str]] = {}  # article -> events
    for e in store.edges:
        if e.rel == "mentions" and store.nodes.get(e.dst, Node("", "", "")).kind == "Subject":
            mentions.setdefault(e.dst, set()).add(e.src)
        elif e.rel == "about" and store.nodes.get(e.dst, Node("", "", "")).kind == "Event":
            about.setdefault(e.src, set()).add(e.dst)
    subj_events: dict[str, set[str]] = {}
    for subj, arts in mentions.items():
        for a in arts:
            subj_events.setdefault(subj, set()).update(about.get(a, set()))
    return subj_events


def derive_shared_subject_links(store: GraphStore, at: str) -> list[Edge]:
    """Connector derivation: events sharing a Subject get linked.

    Untyped mesh, separate from the news loop's typed Hypotheses
    (co_mention/co_location/shared_context/co_outcome): the view
    always filters this mesh so the typed links stay readable.

    An event counts for a subject when an article that mentions the
    subject is also `about` that event. Emits both directions so the
    workspace renders the link either way. Pure: caller records them.
    """
    out = []
    subj_events = _subject_events(store)
    for subj in sorted(subj_events):
        evts = sorted(subj_events[subj])
        for i, e1 in enumerate(evts):
            for e2 in evts[i + 1:]:
                out.append(Edge(src=e1, dst=e2, rel="shares-subject-with", at=at))
                out.append(Edge(src=e2, dst=e1, rel="shares-subject-with", at=at))
    return out


def derive_market_links(store: GraphStore, at: str) -> list[Edge]:
    """Connector derivation: every market links to its event.

    Attribution reads the market's `event_ticker` prop (written by the
    Kalshi publish path); legacy markets without it fall back to the
    ticker prefix (`{event}-*`). Unattributable tickers stay unlinked.
    Pure: caller records them.
    """
    events = {nid.removeprefix("kalshi:")
              for nid, n in store.nodes.items() if n.kind == "Event"}
    out = []
    for nid, n in store.nodes.items():
        if n.kind != "Market":
            continue
        ev = (n.props or {}).get("event_ticker")
        if ev is None:
            t = nid.removeprefix("market:")
            ev = next((e for e in events
                       if t == e or t.startswith(e + "-")), None)
        if ev and f"kalshi:{ev}" in store.nodes:
            out.append(Edge(src=f"kalshi:{ev}", dst=nid,
                            rel="has-market", at=at))
    return out


def followup_tasks(store: GraphStore, event_id: str) -> list[dict]:
    """Connector dispatch: investigate tasks for events related to event_id.

    For every subject of the new event, every *other* event sharing it
    becomes a follow-up task ({event_ticker, terms}) so the news agent
    digs their past too — this closes the topic -> news -> topics loop.
    Terms come from Subject node titles. Pure: caller dispatches.
    """
    subj_events = _subject_events(store)
    mine = {s for s, evts in subj_events.items() if event_id in evts}
    tasks: dict[str, set[str]] = {}
    for subj in mine:
        title = store.nodes[subj].title
        for other in sorted(subj_events[subj]):
            if other != event_id and other.startswith("kalshi:"):
                tasks.setdefault(other, set()).add(title)
    return [{"event_ticker": eid.removeprefix("kalshi:"),
             "terms": sorted(terms)}
            for eid, terms in sorted(tasks.items())]


def refresh_derived_links(api: "GraphUpdateAPI") -> int:
    """Recompute + record all derived links. Idempotent via record().

    Call after every commit batch so the graph never goes stale.
    Returns the number of newly recorded edges (reinforcements don't
    count as new).
    """
    fresh = 0
    derived = derive_shared_subject_links(api.store, api.store.now())
    derived += derive_market_links(api.store, api.store.now())
    for e in derived:
        if api.record(e).reason == "recorded":
            fresh += 1
    return fresh


def subgraph_as_of(store: GraphStore, ts: str) -> GraphStore:
    """Time travel: the graph as of world-time `ts` (valid_at <= ts).

    Undated nodes/edges (valid_at == "") are always included; edges
    touching pruned nodes are dropped. Timestamps compare
    lexicographically, which matches chronological order for the
    %Y%m%dT%H%M%SZ format used throughout.
    """
    snap = GraphStore()
    for nid, n in store.nodes.items():
        if not n.valid_at or n.valid_at <= ts:
            snap.nodes[nid] = n
    for e in store.edges:
        if e.src in snap.nodes and e.dst in snap.nodes and (
                not e.valid_at or e.valid_at <= ts):
            snap.edges.append(e)
    return snap
