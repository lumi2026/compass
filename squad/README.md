# Squad

Agent lanes that turn Kalshi markets + news into graph structure.
All writes flow through the Skeptic gate and commit at one point —
`POST /proposals` on the graph API. No lane talks to the network
services directly; the model-driven brain goes through `tools/`,
lanes take injected data or call `sources/` + `tools/articles.py`.

## Contracts (`__init__.py`)

- `Proposal`: one suggested graph write (agent, action, entity, detail, confidence).
- `Agent`: protocol every lane satisfies (`run(task, context)`).
- `Skeptic`: vetoes weak proposals (confidence < 0.3, empty action/entity). Only approved ones commit.
- `Orchestrator`: protocol fan-out to lanes, gate through Skeptic.
  Used by tests; the live loop instead runs cheaper deterministic
  STAGES in `scripts/news_loop.py`.
- `WorkspaceTask` / `feedback_to_task`: human feedback (`explore | ask
  | challenge`) mapped to a typed task for the queue. Accepted by the
  gate, not yet wired to a live UI loop.

## Lanes (run in this order)

- `scout.py`: discovers one Event node with folded Market props for a
  Kalshi event. Add-node only. `Event.props.markets[ticker]` carries
  live odds; each refresh is a logged batch, so the commit log holds
  the odds history. Offline via `context["markets"]` / `markets_fn`.
- `news_agent.py`: explains a topic with news. N-gram term search over
  snapshots (`run`), history striding (`dig`), fetch + relevance
  filter + summary, then lane-ready Article props + about proposals
  (`graph_proposals`); subjects fold into `Article.props.subjects`.
  `run()` itself stays a log-only digest — only `graph_proposals()`
  output commits.
- `investigator.py`: turns kept articles + market context into one
  Hypothesis node with `cites` edges to its evidence articles and
  `asserts` edges to its claim scope (events, lead market). Every node carries `props.type` in the closed taxonomy
  (`co_mention`, `co_location`, `shared_context`, `co_outcome`).
  Layer-2 transitive metas are no longer written. Offline-first;
  optional LLM refinement via `context["model"]`, extractive title
  otherwise.
- `connector.py`: dispatches follow-up digs so the topic → news →
  topics loop closes (`followups()` wraps `graph.followup_tasks`).
  Event-event links are read-time only, never persisted or
  hand-written — there is no write lane.
- `auditor.py`: third reviewer. Vetoes spurious pairs pre-commit
  (explicit NO only, fail-open), judging the claimed hypothesis type
  (`co_location` needs a shared place, `co_outcome` linked outcomes,
  ...; unknown modes get the generic test). Flags already-committed
  bad links with a reversible `challenges` edge, and the audit loop
  (`scripts/audit_loop.py`) retires vetoed nodes via `POST /retire`
  unless `--no-retire-on-veto` (`--force` re-audits everything).
  Also homes the `--deliberate` multi-turn experiment (verdict:
  DROP — single-shot propose + audit stays default).

## Main agent

- `brain.py`: model-driven loop over the service tools. One JSON
  action per turn (`{"tool", ...}` / `{"answer", ...}`), unknown tools
  and bad JSON get feedback, not retries. `run_goal` budget defaults
  to 10 iterations, `0` = no cap. Launches news work singly
  (`news_run_cycle`) or concurrently over selected event sets
  (`news_run_batch`). `STANDING_GOAL` is the default upkeep pass:
  snapshot graph, diff Kalshi discovery, fan out news cycles, close out
  with a findings note.
- `scripts/run_compass.py`: Compass as a service. Repeats
  `STANDING_GOAL` every `--interval-min` with `--loop`
  (`--max-cycles` bounds demo runs), once without it. Needs
  `--graph-url` + `--kalshi-url`; `--model` pins the model spec.
- `scripts/run_news_agent.py`: single-topic lane demo on three fixed
  phrase-term tasks. Resolves the newest snapshot unless `--snapshot`
  pins one; `--max-articles` caps fetch.

## Support

- `models.py`: resolves which chat model each role uses. Env first
  (`MODEL_FOR_<ROLE>`, then `COMPASS_MODEL` / `MODEL_NAME`), then the
  per-role table (fast model for collectors, reasoning variant for
  graph building / investigation), then provider presets
  (`MODEL_PROVIDER`: meta default, openai, deepseek, custom). Returns
  None with no key so lanes fall back to extractive offline paths and
  tests stay network-free.
- `pipeline.py`: wires scout/news outputs into investigator context
  (`build_investigator_context`), plus change-detection helpers
  (`detect_new_items`, `investigation_triggers`) that decide when a
  re-investigation is worth running. Pure functions, no I/O.
- `tracing.py`: opt-in LangSmith tracing (`LANGSMITH_TRACING=true`
  plus key; key alone never enables it). `squad_span` is a no-op
  without tracing, flushes explicitly with it. Called by the
  `scripts/run_*` runners, so a forgotten setup call means zero runs —
  that exact silence has bitten before.
- `next_steps.py`: autonomous carry-over between cycles
  (`compute_next_steps` pure over recent summaries, `apply_next_steps`
  merges with operator pins). Follow-up endpoints first, challenged
  links get a same-mode deep dig, two quiet rounds trigger one deep
  history round. Consumed by the `news_loop --loop` runner and the
  `news_propose_step` brain tool (which returns the step executable —
  the brain runs it first, then continues); explicit `--mode`/`--event`
  always win.

## How it fits together

```
operator goal
  → brain → tools/ → live services (Kalshi :8080, graph :8090)
  → news_loop STAGES per cycle:
      discover (Scout) → scan → pair → propose (NewsAgent +
      Investigator + Auditor veto) → commit (Skeptic gate)
```

The lanes are the vocabulary; the brain (or the deterministic stage
runner) decides what runs and when. Nothing writes to the graph except
through the gated proposals path.
