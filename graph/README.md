# Graph construction

How the living event graph is built, and why reruns never duplicate it.

## Node kinds and stable ids

Ids are deterministic URIs derived from the thing itself, never random.
Same thing proposed twice maps to the same node.

| Kind | Id | Derived from |
|---|---|---|
| Event | `kalshi:{event_ticker}` | Kalshi event ticker |
| Market | `market:{ticker}` | Folded in `Event.props.markets` |
| Article | `article:{sha1-12}` | Canonical article URL |
| Subject | `subject:{slug}` | Folded in `Article.props.subjects` |
| Hypothesis | `hypothesis:{slug}-{n}` | Owning event (investigator) |
| Hypothesis | `hypothesis:loop-{a}-{b}-{type}` | Ordered pair + closed type (news loop) |
| Hypothesis | `hypothesis:odds-{a}-{b}-{type}` | Odds pair + closed type (news loop) |
| Happening | `happening:{slug}` | Dated key event |
| Audit | `audit:{target}` | Flagged hypothesis id |

Loop pair ids use canonical `a < b` ordering in every pair producer, so
the same pair found via any mode or cycle yields one id per closed
type — a second detection mode creates a sibling claim instead of
refreshing the first in place.

Hypothesis nodes carry `props.type` in the closed taxonomy
(`co_mention`, `co_location`, `shared_context`, `co_outcome`) plus the
trigger `mode`; detectors map their mode onto the type. Layer-2
transitive metas are no longer written: the read path reconstructs
shared-context structure from primitive links instead.

## Edge vocabulary

Only primitive, directly-evidenced edges are persisted:

`about` (Article → Event), `cites` (Hypothesis → Article, evidence),
`asserts` (Hypothesis → Event | Market, claim scope),
`challenges` (Audit → Hypothesis), `relates-to` (fallback).
`supports` is the legacy single rel covering both `cites` and
`asserts`: accepted on read/replay, refused for new writes.

Subject and Market nodes are folded into props: an Article carries its
terms in `props.subjects`, and an Event carries its odds in
`props.markets[ticker]`. `mentions` (Article → Subject),
`shares-subject-with` (Event ↔ Event), and `has-market`
(Event → Market) are therefore fully recoverable and are materialized
at read time only on request (`GET /graph?expand=mentions,markets,mesh`;
default payload is persisted nodes/edges). Writing any folded/derived
node or rel through the gate is refused; legacy snapshots keep
theirs, and replay temporarily allows them to reconstruct old logs.

## Write path: lanes, gate, commit

Agents never touch the store. They emit `Proposal(agent, action,
entity_id, detail, confidence)` through `proposals_to_commit`, which
enforces role lanes:

- `scout`: `add-node`
- `news-agent`, `investigator`, `auditor`: `add-node`, `add-edge`
- `connector`: `derive-links` only (a no-op; the read path derives
  links, so there is nothing to write)

Each commit validates kind/rel/endpoints, applies the Skeptic
confidence gate (min 0.3), then merges: same id plus identical
title/props returns `duplicate: kept existing`; same id with new
content returns `refreshed` in place. Edges are idempotent on
`(src, dst, rel)`: re-laying keeps first-seen `at`, bumps
`seen_count` (`reinforced xN`), and ratchets `props` strength up
(`weight`/`confidence` take the max), so confirmed trails grow
heavier instead of multiplying. `cites`/`asserts` carry
`{weight, confidence}` in `props` (missing weight reads as 1); the
view weights degree and link width by it.

Folded subjects stay discriminating: only anchored (non-genre) terms
land in `Article.props.subjects` (`fold_subjects`, falling back to all
terms when a topic has no anchored term at all).

Follow-up digs fan out only on verdict `created`; re-created
duplicates fan out nothing. Vetoed pairs whose hypothesis already
committed get an `Audit` node plus a `challenges` edge, and the
audit loop retires vetoed nodes via `POST /retire` (which also
prunes legacy derived mesh left stale by the removal).

## Rerun behavior

- Same article re-seen → same `article:{sha1}` node, kept as-is.
- Same pair re-proposed → same hypothesis id, `duplicate` or
  `refreshed` (e.g. an LLM-retitled claim updates in place).
- One article under several hypotheses happens only across
  different pairs: distinct claims sharing evidence, by design.
- A pair found via a second mode refreshes the one hypothesis node;
  the `mode`/`type` props then reflect the latest committing detection.
- The audit loop only visits hypotheses with no verdict yet
  (`audit_state.json`), so repeat passes are no-ops — unless
  `--force` re-audits everything. Fresh vetoes retire by default;
  the audit judges the claimed `type` (legacy typeless nodes get the
  generic test).

## Time and persistence

Nodes carry bitemporal stamps: `valid_at` (world time: article
seendate, happening date, `""` when unknown) and `recorded_at`
(when an agent laid the trail); `created_at` is the legacy alias.
Markets hold live odds in `Event.props.markets[ticker]`; history is
not versioned on the node. Every committed batch appends to
`graph_log.jsonl`, which is the odds series the `?at=` replay reads.

`commit_batch` gates a batch, appends the log, and checkpoints an
atomic `graph_snapshot.json`. Boot loads the snapshot and replays
later log records; `findings.jsonl` stores raw ungated findings
alongside.
