# Squad

Agent lanes that turn Kalshi markets + news into graph structure.
All writes flow through the Skeptic gate. Nothing here talks to the
network services directly — live access goes through `tools/`.

## Contracts (`__init__.py`)

- `Proposal`: one suggested graph write (agent, action, entity, detail, confidence).
- `Agent`: protocol every lane satisfies (`run(task, context)`).
- `Skeptic`: vetoes weak proposals. Only approved ones commit.
- `Orchestrator`: fans out to lanes, gates through Skeptic.

## Lanes (run in this order)

- `scout.py`: discovers Event + Market nodes for one Kalshi event.
  Add-node only. Market nodes carry live odds in props.
- `news_agent.py`: explains a topic with news. N-gram term search
  over snapshots, article fetch, relevance filter + summary (uses
  `tools/articles.py`). `dig()` strides history; `graph_proposals()`
  emits lane-ready Article/Subject/edge proposals.
- `investigator.py`: turns kept articles + market context into one
  Hypothesis node with `supports` edges to its evidence.
- `connector.py`: derives event-event links (idempotent, never
  hand-written) and dispatches follow-up digs so the
  topic → news → topics loop closes.
- `auditor.py`: third reviewer. Vetoes spurious pairs pre-commit;
  flags bad committed links with a reversible `challenges` edge
  instead of deleting.

## Main agent

- `brain.py`: model-driven loop over the service tools
  (Kalshi, graph, news cycle launch). `run_goal` budget defaults
  to 10 iterations, `0` = no cap. Launches `news_loop` cycles
  singly (`news_run_cycle`) or concurrently over selected event
  sets (`news_run_batch`).
- `scripts/run_compass.py`: Compass as a service. Each cycle
  runs the internal `STANDING_GOAL` upkeep pass (scan graph +
  discover Kalshi, build via news cycles, close out with derive
  + findings note), repeated every `--interval-min` with
  `--loop` (default `--max-iterations 0`, uncapped). Without
  `--loop` it runs once. `0` = forever, same convention as
  `news_loop`.

## Support

- `models.py`: resolves which chat model each role uses (env
  overrides, then role defaults). Returns None with no key so
  lanes fall back to extractive offline paths.
- `pipeline.py`: wires scout/news outputs into investigator
  context. Only `graph_proposals()` commits; news summaries stay
  log-only.
- `tracing.py`: opt-in LangSmith tracing (`LANGSMITH_TRACING=true`
  plus key). Project defaults to `compass-dev`.

## How it fits together

```
operator goal
  → brain → tools/ → live services (Kalshi :8080, graph :8090)
  → news_loop STAGES per cycle:
      discover (Scout) → scan → pair → propose (NewsAgent +
      Investigator + Auditor veto) → commit (Skeptic gate +
      Connector derive)
```

The lanes are the vocabulary; the brain decides what runs and
when. The graph API gate (`/proposals`) is the single commit
point — no lane writes to the graph directly.
