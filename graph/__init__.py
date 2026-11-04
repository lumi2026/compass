"""Living event graph: store, gated update API, and derivation.

Thin package surface — every name below is importable straight from
`graph` (back-compatible with the original single-file module):

- graph.store: Node, Edge, GraphStore, id helpers, vocab constants.
- graph.gate: CommitResult, GraphUpdateAPI, ROLE_ACTIONS,
  proposals_to_commit.
- graph.derive: derive_shared_subject_links, followup_tasks,
  refresh_derived_links, sync_derived_links, prune_derived_links,
  subgraph_as_of.

ID scheme (stable URIs, shared by every agent):
  kalshi:{event_ticker}   Event    e.g. kalshi:KXSPACEXMARS-30
  market:{ticker}         Market   e.g. market:KXSPACEXMARS-30-30
  article:{url-sha1-12}   Article  one node per canonical URL
  subject:{slug}          Subject  shared bridge, e.g. subject:spacex
  hypothesis:{slug}-{n}   Hypothesis  investigator claim, numbered per subject
  happening:{slug}      Happening key dated event (market moves, etc.)
  audit:{target}        Audit      auditor flag on a bad link (reversible)

Edge vocabulary:
  mentions             Article -> Subject   (article covers the subject)
  about                Article -> Market | Event  (LLM relevance passed)
  shares-subject-with  Event <-> Event      (DERIVED by Connector only,
    via a shared Subject; never hand-written)
  has-market             Event -> Market      (DERIVED by Connector only,
    from the market's event_ticker prop, ticker-prefix fallback)
  supports             Hypothesis -> Market | Article
  challenges           Audit -> Hypothesis  (auditor flags a bad link)
  relates-to           fallback for anything unclassified
"""

from .cytoscape import MAX_VISIBLE_NODES, to_cytoscape
from .derive import (
    derive_market_links,
    derive_shared_subject_links,
    followup_tasks,
    prune_derived_links,
    refresh_derived_links,
    subgraph_as_of,
    sync_derived_links,
)
from .persist import append_finding, boot, commit_batch, replay_log
from .gate import (
    ROLE_ACTIONS,
    CommitResult,
    GraphUpdateAPI,
    proposals_to_commit,
)
from .store import (
    DERIVED_RELS,
    EDGE_RELS,
    NODE_KINDS,
    Edge,
    GraphStore,
    Node,
    article_id,
    happening_id,
    subject_id,
)

__all__ = [
    "DERIVED_RELS",
    "EDGE_RELS",
    "MAX_VISIBLE_NODES",
    "NODE_KINDS",
    "CommitResult",
    "Edge",
    "GraphStore",
    "GraphUpdateAPI",
    "Node",
    "ROLE_ACTIONS",
    "append_finding",
    "article_id",
    "boot",
    "commit_batch",
    "derive_market_links",
    "derive_shared_subject_links",
    "followup_tasks",
    "happening_id",
    "proposals_to_commit",
    "prune_derived_links",
    "refresh_derived_links",
    "replay_log",
    "subgraph_as_of",
    "sync_derived_links",
    "subject_id",
    "to_cytoscape",
]
