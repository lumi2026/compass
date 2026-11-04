# Graph construction

How the living event graph is built, and why reruns never duplicate it.

## Node kinds and stable ids

Ids are deterministic URIs derived from the thing itself, never random.
Same thing proposed twice maps to the same node.

| Kind | Id | Derived from |
|---|---|---|
| Event | `kalshi:{event_ticker}` | Kalshi event ticker |
| Market | `market:{ticker}` | Kalshi market ticker |
| Article | `article:{sha1-12}` | Canonical article URL |
| Subject | `subject:{slug}` | Watch term or entity |
| Hypothesis | `hypothesis:{slug}-{n}` | Owning event (investigator) |
| Hypothesis | `hypothesis:loop-{a}-{b}` | Ordered pair (news loop) |
| Happening | `happening:{slug}` | Dated key event |
| Audit | `audit:{target}` | Flagged hypothesis id |

Loop pair ids use canonical `a < b` ordering in every pair producer, so
the same pair found via any mode or cycle yields one id.

## Edge vocabulary

`mentions` (Article → Subject), `about` (Article → Market/Event),
`supports` (Hypothesis → Market/Article), `challenges`
(Audit → Hypothesis), `relates-to` (fallback). `shares-subject-with`
(Event ↔ Event) and `has-market` (Event → Market) are derived-only:
only the Connector role may write them, via `derive-links`, and they
are recomputed from Article/Subject evidence on demand.

## Write path: lanes, gate, commit

Agents never touch the store. They emit `Proposal(agent, action,
entity_id, detail, confidence)` through `proposals_to_commit`, which
enforces role lanes:

- `scout`: `add-node`
- `news-agent`, `investigator`, `auditor`: `add-node`, `add-edge`
- `connector`: `derive-links` only (never hand-writes edges)

Each commit validates kind/rel/endpoints, applies the Skeptic
confidence gate (min 0.3), then merges: same id plus identical
title/props returns `duplicate: kept existing`; same id with new
content returns `refreshed` in place. Edges are idempotent on
`(src, dst, rel)`: re-laying keeps first-seen `at` and bumps
`seen_count` (`reinforced xN`), so confirmed trails grow heavier
instead of multiplying.

Follow-up digs fan out only on verdict `created`; re-created
duplicates fan out nothing. Vetoed pairs whose hypothesis already
committed get an `Audit` node plus a `challenges` edge: bad links
are flagged, never deleted.

## Rerun behavior

- Same article re-seen → same `article:{sha1}` node, kept as-is.
- Same pair re-proposed → same hypothesis id, `duplicate` or
  `refreshed` (e.g. an LLM-retitled claim updates in place).
- One article under several hypotheses happens only across
  different pairs: distinct claims sharing evidence, by design.
- A pair found via a second mode refreshes the one hypothesis node;
  the `mode` prop then reflects the latest committing mode.
- The audit loop only visits hypotheses with no verdict yet
  (`audit_state.json`), so repeat passes are no-ops.

## Time and persistence

Nodes carry bitemporal stamps: `valid_at` (world time: article
seendate, happening date, `""` when unknown) and `recorded_at`
(when an agent laid the trail); `created_at` is the legacy alias.
Markets hold live odds in `props`; history is not versioned on the
node. Every committed batch appends to `graph_log.jsonl`, which is
the odds series the `?at=` replay reads.

`commit_batch` gates a batch, appends the log, and checkpoints an
atomic `graph_snapshot.json`. Boot loads the snapshot and replays
later log records; `findings.jsonl` stores raw ungated findings
alongside.
