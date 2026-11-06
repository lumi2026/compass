"""Graph derivation: read-time nodes/links, dispatch, and time travel.

Only primitive edges persist. Subject and Market nodes are folded into
props (Article.props.subjects, Event.props.markets), so `mentions`,
`shares-subject-with`, and `has-market` are reconstructed on read by
`materialize_derived_nodes` / `materialize_derived_links`.
`prune_derived_links` remains for cleaning legacy derived edges left by
older writers.
"""

from typing import TYPE_CHECKING

from .store import DERIVED_RELS, Edge, GraphStore, Node, subject_id

if TYPE_CHECKING:  # avoid gate<->derive import cycle at runtime
    from .gate import GraphUpdateAPI


def _article_subjects(store: GraphStore) -> dict[str, dict[str, str]]:
    """article id -> {subject id: title} from folded props + legacy edges."""
    out: dict[str, dict[str, str]] = {}
    for nid, n in store.nodes.items():
        if n.kind != "Article":
            continue
        terms = (n.props or {}).get("subjects")
        if isinstance(terms, str):
            terms = [terms]
        for term in terms or []:
            if not isinstance(term, str) or not term.strip():
                continue
            sid = term if term.startswith("subject:") else subject_id(term)
            title = term.removeprefix("subject:")
            out.setdefault(nid, {}).setdefault(sid, title)
    for e in store.edges:
        if e.rel != "mentions":
            continue
        dst = store.nodes.get(e.dst)
        if dst is not None:
            if dst.kind != "Subject":
                continue
            title = dst.title or e.dst.removeprefix("subject:")
        elif e.dst.startswith("subject:"):
            title = e.dst.removeprefix("subject:")
        else:
            continue
        out.setdefault(e.src, {}).setdefault(e.dst, title)
    return out


def _subject_terms(store: GraphStore) -> dict[str, str]:
    """subject id -> display title, from props, legacy nodes, or mentions."""
    terms = {nid: (n.title or nid.removeprefix("subject:"))
             for nid, n in store.nodes.items() if n.kind == "Subject"}
    for subjects in _article_subjects(store).values():
        for sid, title in subjects.items():
            terms.setdefault(sid, title)
    return terms


def _event_markets(store: GraphStore) -> dict[str, dict[str, dict]]:
    """event id -> {ticker: market props} from folded Event.props.markets."""
    out: dict[str, dict[str, dict]] = {}
    for nid, n in store.nodes.items():
        if n.kind != "Event":
            continue
        markets = (n.props or {}).get("markets")
        if not isinstance(markets, dict):
            continue
        clean: dict[str, dict] = {}
        for ticker, props in markets.items():
            if not isinstance(ticker, str) or not ticker:
                continue
            clean[ticker] = dict(props) if isinstance(props, dict) else {}
        if clean:
            out[nid] = clean
    return out


def _subject_events(store: GraphStore) -> dict[str, set[str]]:
    """subject id -> events, via article subjects + about edges."""
    subjects = _article_subjects(store)
    about: dict[str, set[str]] = {}  # article -> events
    for e in store.edges:
        if e.rel != "about":
            continue
        dst = store.nodes.get(e.dst)
        if dst is not None:
            if dst.kind != "Event":
                continue
        elif not e.dst.startswith("kalshi:"):
            continue
        about.setdefault(e.src, set()).add(e.dst)
    subj_events: dict[str, set[str]] = {}
    for aid, terms in subjects.items():
        for subj in terms:
            subj_events.setdefault(subj, set()).update(about.get(aid, set()))
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

    Folded markets come from Event.props.markets. Legacy Market nodes
    still attribute through their `event_ticker` prop, or the ticker
    prefix (`{event}-*`) when that prop is missing. Unattributable
    tickers stay unlinked. Pure: caller records them.
    """
    events = {nid.removeprefix("kalshi:")
              for nid, n in store.nodes.items() if n.kind == "Event"}
    out = []
    seen: set[tuple[str, str]] = set()
    for eid, markets in _event_markets(store).items():
        for ticker in sorted(markets):
            dst = f"market:{ticker}"
            key = (eid, dst)
            if key in seen:
                continue
            seen.add(key)
            out.append(Edge(src=eid, dst=dst, rel="has-market", at=at))
    for nid, n in store.nodes.items():
        if n.kind != "Market":
            continue
        ev = (n.props or {}).get("event_ticker")
        if ev is None:
            t = nid.removeprefix("market:")
            ev = next((e for e in events
                       if t == e or t.startswith(e + "-")), None)
        if ev and f"kalshi:{ev}" in store.nodes:
            key = (f"kalshi:{ev}", nid)
            if key in seen:
                continue
            seen.add(key)
            out.append(Edge(src=f"kalshi:{ev}", dst=nid,
                            rel="has-market", at=at))
    return out


def materialize_derived_nodes(store: GraphStore,
                                kinds: set[str] | None = None) -> list[Node]:
    """Read-time Subject and Market nodes implied by folded props.

    Persisted nodes always win (legacy snapshots keep their own
    Subject/Market nodes). Returned nodes are never written to the
    store; callers merge them into display payloads. Pass kinds={...}
    to include only a subset (e.g. {"Market"}); None means all.
    """
    out: dict[str, Node] = {}
    want_subject = kinds is None or "Subject" in kinds
    want_market = kinds is None or "Market" in kinds
    for sid, title in _subject_terms(store).items():
        if not want_subject:
            break
        if sid not in store.nodes:
            out[sid] = Node(id=sid, kind="Subject", title=title)
    for eid, markets in _event_markets(store).items():
        if not want_market:
            break
        if eid not in store.nodes:
            continue
        for ticker, props in sorted(markets.items()):
            mid = f"market:{ticker}"
            if mid in store.nodes:
                continue
            out[mid] = Node(id=mid, kind="Market",
                            title=str(props.get("title") or ticker),
                            props=dict(props))
    return [out[k] for k in sorted(out)]


def followup_tasks(store: GraphStore, event_id: str) -> list[dict]:
    """Connector dispatch: investigate tasks for events related to event_id.

    For every subject of the new event, every *other* event sharing it
    becomes a follow-up task ({event_ticker, terms}) so the news agent
    digs their past too — this closes the topic -> news -> topics loop.
    Terms come from folded Article props or legacy Subject node titles.
    Pure: caller dispatches.
    """
    subj_events = _subject_events(store)
    terms = _subject_terms(store)
    mine = {s for s, evts in subj_events.items() if event_id in evts}
    tasks: dict[str, set[str]] = {}
    for subj in mine:
        title = terms.get(subj, subj.removeprefix("subject:"))
        for other in sorted(subj_events[subj]):
            if other != event_id and other.startswith("kalshi:"):
                tasks.setdefault(other, set()).add(title)
    return [{"event_ticker": eid.removeprefix("kalshi:"),
             "terms": sorted(terms)}
            for eid, terms in sorted(tasks.items())]


def materialize_derived_links(store: GraphStore,
                              at: str | None = None,
                              rels: set[str] | None = None) -> list[Edge]:
    """Read-time folded/derived links; never persisted.

    Synthesizes `mentions` from Article.props.subjects, then the
    Event-to-Event subject mesh (one canonical direction, sorted
    endpoints) and Event-to-Market links. Callers merge these with
    persisted primitive edges, deduping by ``(src, dst, rel)`` so
    legacy edges are not duplicated. Pass rels={...} to include only
    a subset; None means all.
    """
    at = store.now() if at is None else at
    want = rels
    out: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()
    persisted = {(e.src, e.dst, e.rel) for e in store.edges}
    if want is None or "mentions" in want:
        for aid, subjects in sorted(_article_subjects(store).items()):
            art = store.nodes.get(aid)
            for sid in sorted(subjects):
                key = (aid, sid, "mentions")
                if key in seen or key in persisted:
                    continue
                seen.add(key)
                out.append(Edge(src=aid, dst=sid, rel="mentions", at=at,
                                valid_at=art.valid_at if art is not None else ""))
    if want is None or "shares-subject-with" in want:
        for e in derive_shared_subject_links(store, at):
            src, dst = (e.src, e.dst) if e.src <= e.dst else (e.dst, e.src)
            key = (src, dst, e.rel)
            if key in seen:
                continue
            seen.add(key)
            out.append(Edge(src=src, dst=dst, rel=e.rel, at=e.at))
    if want is None or "has-market" in want:
        for e in derive_market_links(store, at):
            key = (e.src, e.dst, e.rel)
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
    return out


def prune_derived_links(store: GraphStore,
                        keep: set[tuple[str, str, str]] | None = None) -> int:
    """Drop legacy derived edges whose evidence is gone. Returns count.

    Only needed for graphs written before derived edges became
    read-time-only. The derived set (subject mesh + market links) is
    recomputed from current article props/about/market attribution, so
    a DERIVED_RELS edge outside it is stale — e.g. mesh laid while a
    since-retired article bridged two events. Persisted primitive
    claims (about, cites, asserts, ...) and folded `mentions` edges are never
    touched. Pass a precomputed key set to skip recomputation.
    """
    if keep is None:
        at = store.now()
        keep = {(e.src, e.dst, e.rel)
                for e in derive_shared_subject_links(store, at)}
        keep |= {(e.src, e.dst, e.rel)
                 for e in derive_market_links(store, at)}
    kept = [e for e in store.edges
            if e.rel not in DERIVED_RELS or (e.src, e.dst, e.rel) in keep]
    pruned = len(store.edges) - len(kept)
    store.edges = kept
    return pruned


def sync_derived_links(api: "GraphUpdateAPI") -> tuple[int, int]:
    """Deprecated write path: derived links are no longer persisted.

    Kept for callers that expect the old ``(fresh, pruned)`` shape.
    Derived links are materialized on read instead, so nothing is
    written and the result is always ``(0, 0)``.
    """
    return 0, 0


def refresh_derived_links(api: "GraphUpdateAPI") -> int:
    """Deprecated no-op: derived links are materialized on read.

    Always returns 0. Use ``materialize_derived_links(api.store)`` for
    the current derived set without touching the store.
    """
    return 0


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
    # Folded endpoints are not stored, so include the read-time nodes
    # implied by the filtered articles/events when checking edges.
    visible = set(snap.nodes)
    visible.update(n.id for n in materialize_derived_nodes(snap))
    for e in store.edges:
        if e.src in visible and e.dst in visible and (
                not e.valid_at or e.valid_at <= ts):
            snap.edges.append(e)
    return snap
