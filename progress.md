# Compass Progress

## Day 1 — Oct 27: Kalshi service (goal: historical + live/websocket, topics, agent subscriptions)

### Built
- `sources/kalshi.py` — events, per-event markets, single + batch order books,
  batch candlestick history. Prices converted from Kalshi `*_dollars` strings
  to integer cents; volumes from `*_fp` strings.
- `services/auth.py` — credential loading from env only, request signing
  (Ed25519 direct, RSA-PSS-SHA256), exact header/payload format per Kalshi docs.
- `services/listener.py` — websocket listener for `ticker`, `orderbook_delta`,
  `trade` on a topic watchlist; reconnect with backoff; REST-poll fallback
  when no keys (the socket handshake requires auth even for public channels).
- `services/feed.py` — thread-safe hub: latest state, per-ticker history,
  SSE bus, webhook fan-out with dead-target auto-discard. `merge_state` never
  blanks seeded detail with empty polls.
- `services/server.py` — standalone service: `GET /health`, `POST /topics`,
  `GET /topics`, `GET /snapshot`, `GET /history`, `GET /stream` (SSE push),
  `POST/DELETE /subscriptions` (webhook push for agents).
- `scripts/kalshi_service.py` — one-command entrypoint (`--event`, `--ticker`,
  `--port`, `--env`, `--seed`).
- `scripts/seed_kalshi.py` + `services/seed.py` — freeze live data to
  `data/seed.json`; service serves it offline via `--seed`.
- `scripts/smoke_kalshi.py`, `scripts/smoke_gdelt.py` — per-source live checks.

### Verified
- 25/25 tests green (`uv run python -m pytest tests/ -q`), all offline fixtures
  except live smoke scripts.
- Live REST: events, markets, order books, candles return real data.
- Live socket on production: signed handshake OK with user keys (inline PEM in
  `.env`), subscription acknowledged by the exchange. Demo endpoint 401s with
  the same keys, so the keys are production keys.
- Live service (poll mode): health, snapshot, history, SSE stream (real pushed
  updates), subscriptions — all probed on a running instance.
- Socket path: subscribe wire format + ticker delivery proven over a real
  local websocket in tests.
- Seeded service: frozen 7-ticker seed served correctly with merge intact.

### Pending (needs user)
- Commit the progress log (`a841517` is the last push).
- Demo topic freeze — recommended: Mamdani market (highest 24h volume seen).
- GDELT side of the original plan (source plugin + smoke exist; graph
  integration untouched).

### Resolved this session
- Keys moved inline into `.env` (`KALSHI_PRIVATE_KEY`); `kalshi.key` deleted.
  python-dotenv added as a dependency; repo env ignores `.env`.
- Market price parsing fixed to Kalshi `*_dollars` strings; seed recaptured
  with real prices.

## Day 2 — Oct 28

### Plan
- News service baseline: behavior, latency, Oct-28 USA sample, how we use it later.

### Built
- `sources/gdelt.py`: 429 backoff 2s-steps → 7s-steps (GDELT asks max 1 req / 5s).
- `docs/gdelt-doc-api.md`: clean DOC 2.0 reference (params, operators, modes, compass usage).
- `scripts/collect_news.py`: background topic watcher (Kalshi-linked defaults:
  Mamdani/NYC mayor, Fed rates, Trump White House, US economy). Polls DOC
  artlist with `--gap-sec` spacing, writes `data/news_digest.json` atomically
  (articles + summary: counts, seendate lag, top domains, title keywords).
  `--once` or `--loop --interval-min 15`. `tests/test_news_digest.py` added.
  28/28 tests green.

### GDELT v1 full-text search notes (from
  https://blog.gdeltproject.org/announcing-the-gdelt-full-text-search-api/)
- Endpoint: `api/v1/search_ftxtsearch/search_ftxtsearch`. Searches English
  translations of 65 languages, last-24h window by default.
- Query commands: `sourcecountry:`, `domain:`, `sourcelang:`,
  `lastminutes:` (multiples of 15), `sortby:date/rel/tone`, `"exact phrases"`,
  ALL-CAPS = theme search. 15–30 min rolling delay stated — not realtime.
- Machine outputs: `output=urllist` (CSV: timestamp, lang, URL),
  `output=timelinecsv` (volume/tone per 15-min bucket). Article lists are HTML.
- Live probe: alive, no 429 (unlike DOC v2), but latency 10–17s/req and index
  looks ~19h stale (newest `sourcecountry:unitedstates` rows stamped
  20261027051500; `lastminutes:360` empty). Treat as legacy/fallback only.
- USA sample (v1, 10 rows, all eng): Trump/White House upkeep story, HSBC Q3,
  Asian markets, NHL, local outlets. Timestamp+URL only, no titles.

### Verified
- DOC v2 artlist probe: `sourcecountry:US`, `timespan=1days`, `sort=datedesc` → 10/10 rows with titles + `seendate`, 33s wall (≥1 retry). Same 10 rows as v1.
- v1 + v2 agree newest US content is `20261027T051500Z` (~19.5h stale at fetch) — pipeline stall or frozen shard; `timelinevolraw` blocked by 429s, recheck later.
- Rate-limit behavior: 429 ("max 1 req / 5s") on most DOC v2 attempts from this network; successes need minutes of spacing. 10–17s latency even on errors.

### Baseline (how we use it later)
- Primary: DOC v2 `artlist` + `format=json`, `sort=datedesc`, 15-min poll via `startdatetime`/`enddatetime` windows, background thread + cache, dedupe by URL hash. Track max `seendate` lag as the freshness metric.
- Intensity signal (when limits allow): `timelinevolraw` on the same query (raw counts + `norm` per 15-min bucket).
- Feed quality: `sourcecountry:US` alone too noisy — add `theme:`/`repeat:`/tone/domain filters.
- v1 full-text API: fallback only (stale index observed).
- Live validation: stubbed end-to-end digest run OK (rc=0, keywords/domains/lag
  correct; lag math independently matches ~20h stale index). Real live
  single-topic runs 429'd through all retries twice (egress throttled) —
  failure path verified instead (error recorded in digest, exit 1).

### Pending
- (carry over from Day 1 unless done)

### NGrams 3.0 (goal: integrate as second news source)
- `docs/gdelt-ngrams.md`: announcement read + live assessment + design.
- Measured: snapshots ~every 15 min (:01/:16/:31/:46), ~23–51 MB / ~1.25M
  records each, static host fast + unthrottled; history back to 2020.
- Design: `sources/ngrams.py` streams snapshots, matches watch-terms, emits
  burst counts (`data/ngram_counts.json`) + provenance snippets into the
  digest. Raw snapshots discarded.
- Next: build the plugin + wire into `collect_news.py` loop.

### Single-agent prototype (news summary for Kalshi topics)
- Topics picked live: `KXWARSHOUT-26JUL` (Warsh/Fed chair, lead 75c),
  `KXSPACEXMARS-30` (SpaceX Mars, 20c), `KXTOPLLMGENERAL-30` (top AI model).
- `squad/news_agent.py`: tools `kalshi_markets`, `snapshot_candidates`,
  `download_snapshot`, `ngram_mentions` (streaming, unthrottled static host),
  `fetch_article` (HTML→text), `summarize` (extractive) + `NewsAgent.run`
  returning squad `Proposal(action=news_summary)`. Snapshot used:
  `20261028014600` (1.03M en records scanned in seconds).
- Ran 3/3 topics end to end, conf 0.7–0.75. `tests/test_news_agent.py`
  added; 32/32 green.
- Known gaps (next): unigram noise (`federal` → Australian politics) needs
  pre/post relevancy filtering; article boilerplate in summaries;
  `KXTOPLLMGENERAL-30` returned 0 markets — check markets query.

### LangChain wiring (Muse as the agent's model)
- Deps: `langchain-core` + `langchain-anthropic` via uv (project `.venv`).
- `squad/models.py`: `make_chat_model()` — `ChatAnthropic` from
  `ANTHROPIC_API_KEY` + `COMPASS_MODEL` (default claude-sonnet-4-5), else None.
- `NewsAgent` gains `model="auto"`: per-article `llm_summarize` with
  analyst prompt; any failure falls back to extractive; bullets tagged
  `[llm]`/`[extractive]`, `engine=` line in detail. No key → fully offline.
- Tests: factory-None, llm-used, llm-failure-fallback added; 35/35 green.
- Switched to Muse Spark: `langchain-openai`'s `ChatOpenAI` against
  `https://api.meta.ai/v1` (model `muse-spark-1.3`), key `MODEL_API_KEY`;
  `langchain-anthropic` removed. Suite still 35/35 (no key in env →
  extractive path).
- To activate: user adds `MODEL_API_KEY=...` to `.env` (untracked).

### Pre-LLM smoke (`scripts/smoke_news_pipeline.py`)
- Kalshi event → snapshot resolve → n-gram hits → fetch articles → dump
  text to `/tmp/smoke_articles/`. Run on `KXSPACEXMARS-30` + spacex/mars:
  lead 20c, 4 hits / 3 urls, 3/3 articles ~1000 words each. SMOKE OK.
- Notes: newest snapshot marks 404 (latest usable ~01:46 UTC, ~1h lag);
  `mars` unigram had 0 hits in-snapshot. Fixed `--term` append-default bug.

### Multi-gram matching (doc re-check)
- `match_record` implements the NGrams 3.0 phrase rules: ngram is the
  lower-middle word (bigram: FIRST word per doc), neighbors verified in
  pre-tail/post-head; type-2 (chars) excluded for now.
- Live proof on `20261028014600`: "white house" 37 hits/11 urls, "new york"
  104/51, "interest rates" 12/7, trigram "artificial intelligence" 17/12 —
  vs unigram `federal` 210 noisy. `test_multi_gram_matching` added; 36/36.

### Phrase-based agent re-run (`scripts/run_news_agent.py`)
- Tasks use phrases: Warsh→(kevin warsh, federal reserve, rate cut) 7 hits;
  SpaceX→(spacex, starlink, elon musk) 13 hits; AI→(artificial
  intelligence, openai, anthropic) 20 hits. All conf 0.85, extractive.
- Better (Fed data, Starlink spyware) but still mixed: "elon musk" pulls
  Texas-politics pieces; crime blotter via `openai` unigram. Next lever:
  per-article relevance score (pos decile + term density) before fetching.

### Fan-out accumulator (plan)
- Agent fans out n-gram search over a past period in the background,
  accumulates + parses news into a shared SQLite store (`data/news.db`,
  git-ignored) that agents query: snapshots log, mentions (term/ts/url/
  pre/post/pos), articles (url/text/summary/engine).

## Day 2 (cont.) — Oct 28

### Plan
- Fan-out accumulator: `squad/store.py` (SQLite: snapshots/mentions/articles),
  `scripts/accumulate_news.py` (`--hours-back` backfill + `--loop`,
  per-snapshot article cap, snapshot cleanup), tests, short live backfill.
- Relevance scoring (pos decile + term density) before article fetch.
- Wire digest/accumulator output into Investigator context for Kalshi moves.
- Later / non-essential: extra LLM providers (GPT/DeepSeek presets exist in
  `squad/models.py`; Claude/Anthropic support deferred — needs
  `langchain-anthropic` re-added). Stay on Muse Spark for the demo.
- Live key smoke OK: `muse-spark-1.3` via api.meta.ai/v1 replied `OK`.
- Full agent run OK (snapshot 20261028224600, 404 on newest as usual):
  Warsh 3/3 kept, all `[llm]`, on-topic (Fed holds 3.75–4%, Warsh majority,
  Dec hike priced); SpaceX 3/3 kept but off-question (xAI revenue, Skydance,
  Starlink vessels — nothing on Mars landing); AI topic 0 markets (known gap),
  filter dropped 3/3, conf 0.4.
- Fix: `llm_relevant` now takes the lead market title (`question`) and
  judges against the QUESTION — YES also covers substantive news about
  central actors; 16/16 tests green (incl. prompt-carries-question).
- Validated live (subject-level prompt, --max-articles 3): Warsh 3/3 kept
  (`engine=llm:muse-spark-1.3`); SpaceX 2/3 kept with contributor
  (`engine=llm:muse-spark-1.3-contributor`, maritime-vessel piece dropped);
  AI 0 markets, dropped 3/3.
- Default model now `muse-spark-1.3-contributor` for all roles (role table
  kept for later split); role env (`MODEL_FOR_<ROLE>`) + `COMPASS_MODEL`
  overrides unchanged. Suite has run green repeatedly since (62/62).
- Code restructure (reviewed + applied): `squad/tools.py` (fetch/summarize/
  llm_* — shared by all agents), `sources/ngrams.py` (snapshot/match/count),
  `squad/news_agent.py` slimmed to `NewsAgent` + kalshi adapter, `Agent`
  protocol task type fixed to `dict | str`. Scripts + tests re-imported.
  Full suite: 47/47 green.
- NGrams now the default news source (`--source ngrams`, DOC kept as
  `--source doc` fallback): `collect_news.py` resolves the newest snapshot
  once per round, maps topics to phrase terms (`DEFAULT_NGRAM_TERMS`),
  converts samples to `NewsArticle`s (snippet title, record-date seendate,
  domain from URL) and reuses `summarize_topic`. 6 new tests; 51/51 green.
- Live digest proof (snapshot 20261028233100): fed 13 hits/6 urls, trump
  17/6, economy 26/5, all lag ~43 min (vs ~19h DOC staleness); mamdani 0
  hits in this 15-min window — watch whether the terms need widening.
- Snapshot 404s avoided: `snapshot_candidates` starts 10 min back (freshest
  mark hasn't landed), new shared `sources/ngrams.py::resolve_snapshot`
  used by both scripts; verified live — first candidate hit, no miss lines.
  52/52 green.

### Event graph design + POC (new goal)
- Schema (`graph/__init__.py`): stable URI ids
  (`kalshi:`, `market:`, `article:{sha1}`, `subject:`, `hypothesis:`),
  `Subject` node kind, edge vocab (`mentions`, `about`,
  `shares-subject-with` derived-only, `supports`, `challenges`,
  `relates-to`); endpoint + kind validation in the gate; JSON save/load.
- Collective authorship: `ROLE_ACTIONS` lanes (scout/investigator/
  news-agent/connector), `proposals_to_commit` translator (JSON detail),
  `derive_shared_subject_links` (article-mediated event pairing).
- POC (`scripts/graph_poc.py`, offline): 7 seed markets in 3 families + 3
  fixture articles → 17 nodes / 17 edges; Elon↔NATO linked via
  `subject:spacex`, pope control isolated; `to_cytoscape` OK (34 elements).
  55/55 green.
- Interplay spec (specified, live watcher later): digest articles + Kalshi
  price shifts → `detect_new_items` (`new_article` / `price_shift`) →
  `investigation_triggers` → `Orchestrator.investigate({event_ticker,
  terms})` → gated proposals → commit. Live price-shift watcher deferred.
- Interplay wired (offline): `NewsAgent.graph_proposals` turns kept
  articles into lane-ready add-node/add-edge proposals (canonical
  `article_id`/`subject_id` now in graph layer, POC reuses them);
  interplay test gates them through Skeptic → commits → Connector derives
  the cross-event link. 56/56 green, POC still OK.
- Use-case fixes built (all four): edge dedupe in `record()` (re-runs
  keep first-seen `at`); bounded backfill (`select_snapshots` stride +
  `backfill_snapshots` ≈13 files/48h, `NewsAgent.dig` deduped by URL,
  `scripts/backfill_news.py` digest + proposals); Connector dispatch
  (`followup_tasks` — POC prints NATO→Elon/spacex follow-up);
  `refresh_derived_links` (idempotent) + `save`/`load` wired through POC
  roundtrip. 62/62 green.
- Model switch: `COMPASS_MODEL=muse-spark-1.3-contributor` smoke OK live
  (`OK` via api.meta.ai/v1). Swap = one env line, no code change.
- Per-agent models: `make_chat_model(provider, model)` + new
  `resolve_model(spec)` (auto|None|model-id|{provider,model}|instance);
  `NewsAgent.run` resolves `task["model"]` over agent default; engine line
  now tags the model id (`engine=llm:<id>`). 18/18 tests green.
- Subject-level relevance: filter keeps articles substantively about a key
  subject without requiring they answer the question (SpaceX news counts
  for a Mars question); validated live (see above).

### Start-of-day review (plan vs progress)
- Original plan (`docs/plan.md`): situation-aware consultant, Living Event
  Graph + Squad (Scout/Investigator/Connector fan-out, Skeptic gate, Graph
  Update API), Kalshi + GDELT DOC 2.0 sources, service-ready backend, seeded
  offline demo, Cytoscape workspace + human feedback.
- Day 1 fit: Kalshi source + service + seed — on-plan (backend + offline demo).
- Day 2 fit: GDELT baseline + NGrams 3.0 pivot (DOC v2 throttled, static host
  fast — justified extension, not drift), single news-agent prototype with
  Muse Spark via LangChain — on-plan toward Squad/Investigator.
- Gaps to close for demo (updated at Day 3 close): Event Graph + Graph
  Update API done (schema, lanes, translator, POC). Remaining: full
  Scout/Investigator/Connector agent split (roles exist, classes don't),
  Investigator context wiring, Cytoscape workspace + feedback loop demo.
  Accumulator DB deferred — graph JSON covers shared state for now.

### Meta API wiring (Muse Spark via LangChain)
- `squad/news_agent.py`: new `llm_relevant` tool (YES/NO + reason,
  fail-open) + wired into `NewsAgent.run` before summarization;
  detail gains `relevant_filter=dropped N of M`.
- `tests/test_news_agent.py`: +3 tests (NO filters, unparseable keeps,
  agent drops irrelevant); 11/11 green.
- Key setup (resolved): `MODEL_API_KEY` added to `.env`; smoke + full
  agent runs done (see Day 3 entries above).
- Provider-swap review: `squad/models.py` factory generalized —
  `MODEL_PROVIDER` (meta|openai|deepseek|custom) + presets, generic or
  provider keys, `COMPASS_MODEL`/`MODEL_BASE_URL` overrides; agent tools
  already provider-agnostic (`model.invoke` only). 15/15 tests green.

### Day 2 close-out
Done: Meta/LLM wiring + live validation, per-agent + role-based models
(contributor default), subject-level relevance, squad/tools + ngrams split,
ngrams-as-default digest, 404 avoidance, graph schema + POC + use-case
fixes (dedupe, backfill/dig, dispatch, persistence). 62/62 green.
Not done (moved to Next step): SQLite accumulator + loop service,
Investigator agent + digest wiring, heuristic pre-filter, full squad agent
split, workspace + feedback demo.

### Next step
- Investigator agent: consumes digest/graph context for Kalshi moves;
  wire `graph_proposals` output into its context.
- Squad split: real Scout / Investigator / Connector classes behind the
  existing `ROLE_ACTIONS` lanes + `Orchestrator` fan-out demo.
- Workspace demo: live Cytoscape view + human feedback → task loop.
- Optional / deferred: SQLite accumulator + `--loop` service (graph JSON
  suffices for now); heuristic relevance pre-score (saves LLM calls);
  mamdani term widening; `KXTOPLLMGENERAL-30` zero-market gap.

## Day 3 — Oct 29: squad split + LangSmith

### Built
- Squad split: real Scout / Investigator / Connector agents behind the
  existing role lanes, with Investigator context wiring and a
  `run_squad.py` end-to-end demo (live with seed/snapshot fallback).
- LangSmith: opt-in tracing on SDK env names, default project
  `compass-dev`, wired into both runners.

### Verified
- Full suite green, graph POC OK, live squad run OK, tracing live
  probes ingested. Pushed (`b8d836a`).

### Day 3 close-out
Done: squad split + LangSmith.
Not done: Cytoscape workspace + feedback demo; accumulator + `--loop`
(deferred); heuristic pre-filter; mamdani widening;
`KXTOPLLMGENERAL-30` zero-market gap.

## Day 3 (cont.) — Oct 29: real-data MVP

### Built
- Cross-category seed: `seed_kalshi.py --event` (repeatable) captures
  explicit events instead of whatever tops the live list.
- `run_mvp.py`: multi-event runner over one shared graph (Scout → news
  scan → Investigator per event, Connector derive once).
- `workspace/viewer.py`: standalone Cytoscape page (no server) showing
  the graph as shaped; output lives in `demo/`.

### Verified
- Live: 4 tickers seeded (Fed, space; AI event has zero markets —
  known gap), 27 articles kept, 48 nodes / 137 edges, page renders
  (3 clusters, no cross-links — term sets are disjoint).
- Suite: 80/80 green (incl. new `test_mvp.py`).

### Not done
- Human feedback loop on the workspace; accumulator + `--loop`
  (deferred); heuristic pre-filter; `KXTOPLLMGENERAL-30` zero-market gap.
- Changes uncommitted (MVP scripts, viewer, demo page, seed, docs).

## Day 3 (cont.) — Oct 29: dates + 3D graph

### Built
- News summaries carry dates: run-date line plus per-article stamps.
- Graph page is now 3D (pinned three.js + 3d-force-graph): drag to
  rotate, scroll to zoom, hover for full title, click for details,
  labels toggle. Output still in `demo/`.

### Verified
- Suite green (84 tests, incl. date + 3D viewer coverage).
- Headless browser run clean: canvas renders, zoom/rotate confirmed
  in screenshots, no console errors.
- Hover/click probing inconclusive in automation (synthetic events);
  needs a human pass in a real browser.

### Not done
- Display refinement (layout, label density, mobile) — later step.
- Changes uncommitted.

## Day 4 — Oct 30: unit 1 (graph timestamps + trail weight)

Done: bitemporal fields on Node/Edge (`valid_at` + `recorded_at`,
legacy `created_at`/`at` kept and auto-filled; old JSON loads with
defaults), edge re-lays bump `seen_count`/`last_seen_at` instead of
silent dedupe (`reinforced xN`), `valid_at` passthrough in
`proposals_to_commit`, pure `subgraph_as_of(store, ts)` time-travel
helper. Suite 91/91 (new `tests/test_graph_times.py`; one
contracts test updated to the intended reinforcement behavior);
offline POC still OK.
Not done (next units): append-only log + snapshot, then graph API
service.

### Cross-topic link test (30 topics × 100 snapshots)

Ran `scripts/cross_link.py`: 30 live Kalshi topics, 96 readable
snapshots (~25h), union term scan at ~10s/snapshot after grouping
the matcher by middle word. Result: 7 topic pairs sharing articles
(NFL clusters, a genuine GDP–GDP link, Mamdani × unemployment),
each as a Hypothesis with article evidence; graph 564 nodes / 1333
edges, viewer page renders all of it (fixed a cap bug that dropped
all edges on big graphs). Suite 95/95. Artifacts:
`demo/cross_graph.json` + `demo/cross_graph.html`. Pushed
(`2d7da5b`). User verdict: promising examples found — calling it
a day.

### Day 4 usage (approx)

~3.5M tokens this session (cumulative run counter, last observed
3.1M before the final stretch). Heaviest turns: 3D viewer rewrite
with brace-escaping repairs + headless browser checks, and the
cross-link script build. Local compute (100-snapshot scan, ~40 min
wall) and browser runs cost machine time, not tokens. No paid API
calls beyond existing keys (Kalshi public REST, GDELT static host,
CDN fetches in the browser check).

## Next — living loop milestone (graph builder is the product)

Plan saved to `.agents/plans/2026-10-30-living-loop.md` (direction set,
not started): interconnecting seemingly unrelated Kalshi topics is the
core — a background team of news agents explores *across* topics
(co-mentions, shared subjects, burst correlation, entity overlap),
extracts dated key events, and infers relations only with article
evidence (hypotheses through the gate, per the agent prompt contract).
Two storage layers: a centralized SQLite news store (every finding
with valid + recorded timestamps, backing time travel via
`GET /graph?at=`) under the curated graph. Backend first (central
store, graph API, Kalshi discovery/publish, news team); React + Vite
view after. Graph API serializes concurrent writers (lock + atomic
save). Deployment planned ahead on Fly.io (backend first): one
`compass-backend` app, graph API + Kalshi + news loop as process
groups on one Machine, `compass_data` volume for `news.db` + graph
JSON, secrets via `fly secrets`, only the graph API public (Bearer
token required before any public deploy); frontend deploys
separately. Graph state lives only in the backend (graph API owns
the store in memory, atomic volume checkpoints, `revision` counter
on responses); the frontend is a stateless view reading `/graph`
(+`?at=` time travel) and `/news`/`/happenings` evidence endpoints —
never the files — with agent writes and later human feedback sharing
the same gated proposals path. Storage starts simple and
append-only (ants accreting trails): `graph_log.jsonl` +
`findings.jsonl` + atomic `graph_snapshot.json`, re-committed edges
gain `seen_count` (trail reinforcement); SQLite is the later
migration, not step 1. Data model settled: bitemporal,
event-sourced property graph (reified hypotheses, `Happening`
nodes, `seen_count` trail weight). Kalshi odds are the belief
signal: Market nodes carry live odds, price moves ≥ threshold
become `market-move` happenings, co-moving odds nominate topic
pairs for news exploration (odds suggest, news explains), and
news-vs-odds divergence triggers re-investigation.

## Day 5 — Oct 30: living-loop steps 1 + 2

Built (storage v1, no SQLite yet — JSONL is the later migration base):
- `graph/store.py`: `Happening` kind + `happening_id()` helper.
- `graph/gate.py`: `GraphUpdateAPI.revision`, atomic `save`
  (temp + rename), `load` accepts envelope + legacy plain shape.
- `graph/persist.py` (new): `commit_batch` (Skeptic confidence
  gate + role lanes, derived refresh, log append, snapshot),
  `append_finding`, `replay_log`, `boot` (snapshot + replay).
- `services/graph_server.py` (new, FastAPI + Pydantic, :8090):
  `GET /health`, `GET /graph` (+`?at=` time travel,
  `ETag`/`If-None-Match` 304), `POST /proposals` (per-proposal
  verdicts, wrong-lane refusal), `POST /findings`. One lock,
  snapshot-copy reads. Deps: `fastapi` + `uvicorn` + `httpx`
  via uv. Run: `uv run python -m services.graph_server`.
- `tests/test_graph_api.py` (new): lanes, vetoes, Happening,
  boot/replay, legacy load, findings, `?at=`, 4-thread overlap
  equals sequential, TestClient HTTP roundtrip + validation/422.

Verified: 104/104 green; POC OK (17/17, spacex link, control
isolated); legacy `demo/mvp_graph.json` loads (48/137, rev 0);
live boot on :8099 served health + a proposals commit over HTTP.
Layout settled: graph API :8090 owns store + `data/graph_*`
(git-ignored); kalshi :8080; news loop as `scripts/news_loop.py`;
view reads :8090. `COMPASS_DATA_DIR` (or `--data-dir`) locates
state — same image runs on the Fly `/data` volume later.
Next: step 3 (Kalshi discovery/publish) then step 4 (news loop).

All 13 CLIs (`scripts/*`, graph API) migrated from argparse to
click; flags and defaults unchanged; 104/104 green, POC OK.

Concurrency proven for 10–20 agents (`/tmp/stress_graph.py`,
re-runnable): 20 writers × 15 overlapping batches + 3 readers vs
live uvicorn — 300/300 batches committed, 1801 nodes / 300 edges,
revisions contiguous, snapshot + log intact, zero 500s. p50
post latency 470 ms under full contention (lock queueing, as
designed), 45 batches/s — far above one batch per agent cycle.

Step 3 (Kalshi discovery/publish) done in `services/server.py`:
`GET /events`, `POST /topics {auto, top_k, scan_limit}` (volume-ranked,
per-event failures skipped), `POST/GET /publish/graph`. Each merged
update publishes Scout Event/Market nodes (idempotent) plus a
`market-move` Happening + `relates-to` edge when the move clears the
threshold (default 5c; first sighting is a baseline, not a move).
One in-flight POST at most; graph down = log-only, feed unaffected.
`tests/test_kalshi_publish.py`: 112/112 green. Live proof vs real
graph API: baseline rev 1, +1c rev 2 (no happening), +8c rev 3
with Happening `moved 55c -> 63c` + edge.

Odds on nodes (follows step 3): `Node.props` (live
`price_cents`/`yes_bid`/`yes_ask`/`volume_24h`/`odds_at`) flows
through commit (create/duplicate/`refreshed` with prop merge,
`created_at` kept), `proposals_to_commit`, JSON save/load, and
`to_cytoscape`. Scout Market proposals and the Kalshi publish path
emit odds; Happenings carry structured move props
(`market`/`old_cents`/`new_cents`/`move_cents`) + `valid_at`.
History model: nodes hold live odds only; every refresh is a logged
batch, so `graph_log.jsonl` is the odds series the later `?at=`
time travel replays. 117/117 green; live: create 55c →
`refreshed` 63c visible in `/graph`; legacy `demo/mvp_graph.json`
(48 nodes, no props) still loads.

No-wait publish: `POST /publish/sweep` pushes every watched ticker's
current REST-polled odds in one batch (baselines, never moves).
Live end-to-end on prod API: auto top-2 → 8 busy tickers swept →
rev 1, 10 nodes, markets carrying live `price_cents`/`yes_bid`/
`yes_ask`/`odds_at`. 119/119 green. Note: prod socket stayed quiet
on the seed tickers during the window — sweep (REST) is the
deterministic path; ticks remain the move-detection path.

Step 4 (news loop) done: `scripts/news_loop.py` (click, single
process, threaded fan-out, `--once`/`--loop`, `--event` slice,
`--mode` pin). Each cycle: kalshi discovery → rotating mode
(co-mention, shared-subject, burst, entity-overlap,
odds-correlation) → snapshot scan → `NewsAgent.graph_proposals`
(now with `valid_at` from seendate) + LLM-or-extractive Hypothesis
with `supports` evidence edges → Skeptic → POST /proposals →
connector `derive-links` → POST /findings. `GET /markets?event=`
added to the kalshi service (no watchlist side effects) for the
odds mode, which correlates lead-market 24h closes (|r|≥0.7).
`tests/test_news_loop.py`: incl. an offline end-to-end cycle
(co-mention → hypothesis committed), a followup fan-out test
(new Hypothesis → 2 follow-ups + derived cross-links), and a skip
test for the late-publish 404 found live. Follow-ups key off
freshly *created* Hypothesis verdicts (explicit derive-links
re-derives are idempotent, so `derived_fresh` alone can't signal
newness). `--max-cycles` bounds loop runs for demos. Live: all 5
modes run clean against prod; findings logged; pairs 0 — verified
genuine (shared terms have 0 hits in the current snapshot; even GDP
candles are empty right now). Fixed live: odds mode queried
event tickers for history (always empty) — now resolves lead
market tickers via the new `GET /markets?event=`. 131/131 green.

2- to 5-gram search: matcher was already length-generic
(lower-middle word rule, no caller caps); locked in with
`test_four_five_gram_matching` (105/105). Live proof on
`20261030194600`: "new york city mayor" 2 hits/2 urls,
"secretary of state marco rubio" 15/11; controls "white house"
43/29, "interest rates" 11/4, "artificial intelligence" 38/25.

## Next (ordered)

1. **Live interconnect proof — DONE 23:43 UTC.** 50 events ×
   newest snapshot → 10 pairs, 482 proposals, rev 22, 20 followups.
   Graph now holds 10 LLM-written Hypotheses (GDP cluster cites the
   INEGI Q3 report; Mars × Mamdani via Musk), 34 derived
   `shares-subject-with` edges, `supports` evidence edges. LLM and
   tracing gaps closed the same night: the loop never called
   `setup_langsmith` (so zero runs despite the opt-in flag) and only
   calls the model when pairs exist (all earlier cycles had none —
   correct, not a bug). Now: per-cycle `news-loop-cycle` spans,
   `langsmith=on project=compass-dev` at startup, and flushed
   stage logs (topics/terms → hits → pairs → posted/rev/followups)
   to stdout.
2. **Step 5 — React view** (`view/`): `react-force-graph-3d`
   polling `GET /graph` (+`?at=` slider), same color/shape language,
   empty/stale states (never a blank page). Highest-risk check stays
   the stale/empty-API case — build that first, not last.
3. **Time-travel replay.** `GET /graph?at=` filters nodes today but
   shows live odds; implement replay from `graph_log.jsonl` so odds
   scrub with the slider. The loop already records everything the
   replay needs (`valid_at`, move props, per-batch log).
4. **Step 6 — wiring + docs.** `docs/live_test.md` multi-terminal
   run (graph API → kalshi → 2 loops → view) + the interconnect
   demo script (seed 3 topics, run team, scrub time).
5. **Pre-public gate.** Bearer-token auth on all non-`/health`
   graph routes (`COMPASS_API_TOKEN`) — the current no-auth local
   API must not ship as-is. Then Fly.io backend deploy + staging
   smoke (`/health`, `/graph`, one proposals round-trip).
6. **Polish (as needed).** Heuristic relevance pre-filter to save
   LLM calls; mamdani term widening; `KXTOPLLMGENERAL-30` zero-market
   gap; human feedback loop on the workspace; Cytoscape label
   density/mobile refinement.
7. **Later migrations (explicit non-goals now).** SQLite central
   store when JSONL replay gets slow; multi-machine writers;
   snapshot compaction/retention; webhook push graph → agents.
8. **Experiment: multi-turn pair deliberation (next, planned
   2026-10-31).** Question: does folding propose + audit into one
   multi-turn chat raise the pair success rate vs today's
   single-shot calls? One chat session per pair — turn 1 proposes
   the hypothesis with evidence, turn 2 audits it in the same
   context, optional turn 3 revises or drops. Measured on a fixed
   fixture set labeled from the 11 live pairs (known-good GDP
   same-story vs known-bad Mexico-article): veto precision + keep
   recall vs single-shot, challenge rate, tokens per pair (~3×
   today). Adopt behind a `--deliberate` flag only if precision
   rises without recall loss; otherwise keep single-shot propose
   + separate audit.

## 2026-10-31 ~00:30 UTC — Auditor agent (third review role)

- New `squad/auditor.py`: `audit_pair()` judges one linked pair
  against its shared evidence. Veto ONLY on clear country/market/
  entity mismatch (Mexico article linking US GDP markets); same
  office/type across countries is a keep. Fail-open everywhere
  (no model or unparseable reply keeps the link).
- Gate: `ROLE_ACTIONS["auditor"] = ("add-node", "add-edge")`;
  new `Audit` node kind (red in the viewer); `challenges` is now
  `Audit -> Hypothesis`. Bad links are flagged, never deleted —
  hard removal (tombstones) stays a later migration.
- Loop: every pair audited pre-commit (veto = skip, logged with
  reason); vetoed pairs with an already-committed hypothesis get
  an Audit node + `challenges` edge; `vetoed` count in summary,
  findings, and logs.
- Live: 3 pairs → 2 vetoes with exactly the reported failure
  ("Shared article is only about U.S. GDP growth with no
  Israel/Netanyahu succession content"), 1 genuine GDP pair kept;
  0 audit nodes written (nothing pre-existing to challenge).
- Backfill dry-run over 10 committed hypotheses (no posts): 4 VETO,
  6 KEEP. Two caveats: (a) the auditor is inconsistent on the
  GDP cluster — same INEGI story kept on 34×36, vetoed on 35×36;
  (b) it vetoed the Israel×UK link you called fine, on evidence
  grounds (shared snippet is about Modi's Canada visit, not either
  succession race) — possibly righter than first impressions.
- Open: posting `challenges` for the vetoed existing links awaits
  your call; 136/136 green.

## 2026-10-30 ~23:45 UTC — LLM + LangSmith verified working

- Tiny direct probe: `resolve_model("auto", role="investigator")`
  → `ChatOpenAI`, returned a real sentence + conf 0.8. Key is good.
- Live loop cycle called the model 10× (one per pair); all 10
  Hypotheses in the graph are LLM-written, evidence-grounded
  (e.g. INEGI Q3 numbers appear in both articles and hypotheses).
- LangSmith: `news-loop-cycle` spans landing in `compass-dev`
  (setup + span added to `news_loop.py`; startup prints
  `langsmith=on`). Prior silence explained: no setup call, and no
  pairs to hypothesize about — both closed, no key/flag changes
  needed (flag was already `true`, key present in `.env`).

## Usage log (loop + LLM, running tally)

- 2026-10-30 ~23:45 UTC: 11 model calls total (1 direct probe +
  10 in the first interconnect cycle, one per pair). Cycle
  summaries and findings now carry `llm_calls` (0 when the model
  is None or the mode is odds-correlation, which is pure math).
- Same night: graph at 527 nodes / 159 edges / rev 40 after the
  435-market sweep + 1 loop cycle (482 proposals); 46 commit-log
  records; 12 snapshots cached locally (~250 MB, all re-usable).
- Cost shape per cycle: 1 LLM call per pair (short prompt +
  one-sentence reply), 1 snapshot scan per term-set, 1 graph POST
  per cycle + 1 for derive. Token-level accounting lives in
  LangSmith under `compass-dev`.

## 2026-10-31 ~01:30 UTC — Item 1 deliberation experiment: verdict DROP

- Live both-arms measurement on all 11 PAIR_FIXTURES
  (`/tmp/measure_deliberation.py`, 54 sequential reasoning-model
  calls, ~1h wall — the "hang" was real work, not a stall; output
  was hidden behind the `| tail -30` pipe, now known).
- single-shot: precision=1.00 recall=1.00, 22 calls
  (tp=1 tn=10 fp=0 fn=0).
- deliberated (multi-turn + audit, 3 turns on vetoes):
  precision=1.00 recall=1.00, 32 calls (same confusion matrix).
- Verdict: DROP — deliberation adds ~45% more calls with zero
  accuracy gain on these fixtures. Single-shot stays the default;
  `--deliberate` remains an opt-in experiment flag only. Fixtures
  are ceilinged (10/11 are obvious negatives); revisit only with
  harder near-miss fixtures.
- Tracing note: measurement-script calls DID land in LangSmith
  (`compass-dev`, verified via API — `00:22 UTC` batch of `llm
  ChatOpenAI` runs) through env auto-instrumentation, even though
  the throwaway script never called `setup_langsmith()`. Smoke
  test just now confirmed a fresh traced call appears
  (`01:23 UTC`). If the dashboard looks empty, check project
  `compass-dev`, not the default project.
- Usage note: model calls bill to the Meta key (`MODEL_API_KEY`
  → `api.meta.ai/v1`, provider `meta`), so they show on the Meta
  dashboard — not on any OpenAI/LangSmith usage page. Tonight:
  54 measurement calls + 2 probes.
- 2026-10-31 session (item-1 closeout): goal token counter went
  ~11.77M → ~13.29M across the measurement wait, both-arms
  scoring, tracing verification (1 smoke call + LangSmith API
  reads), deliberate-mode docs, and this commit. Model calls this
  session: 54 measurement + ~4 probes/smoke (all Meta key,
  `compass-dev` traces).

## 2026-11-01 — Step 5 React view (view/)

Built `view/` (Vite + React, vanilla `3d-force-graph@1.80.1` mounted
via ref — same pinned lib as the standalone viewer, no wrapper peer
risk): dev proxy `/graph`+`/health` → `:8090` (no CORS change, no
auth yet), 5s poll with `If-None-Match` skipped when `at` is set
(ETag is global-revision only), client cap 200 by degree with
"showing X of Y", positions preserved across polls, Market/Happening
`props` in the details panel. As-of box is honestly labeled
`valid_at` filter — odds stay live until replay lands.
Verified: `npm run build` clean, 141/141 green, live API rev 80
(529 nodes / 168 edges) serving through the dev proxy (`/health` +
`/graph` 200). No Chromium on PATH — canvas pixels unverified,
reachability only. Next: replay, wiring demo, auth gate.

## 2026-11-02 — Stack startup Q&A (no code changes)

- Resumed from Nov 1 close-out (clean tree at `402fe32`, nothing
  listening). Verified loop/kalshi CLI flags (`--sync-topics` present).
- Clarified wiring questions: the loop reads Kalshi (`GET /events`,
  `GET /markets`) and writes the graph API (`POST /proposals`,
  `POST /findings`); its only write to Kalshi is `sync_watchlist`
  (`POST /topics`), and the Kalshi terminal stays quiet by design
  (`log_message` silenced). Kalshi→graph odds publishing stays off
  until `POST /publish/graph` + `POST /publish/sweep` are run by hand.
- Handed out the 4-terminal startup order (graph :8090 → kalshi :8080
  bare → loop `--once` then `--loop` → view `npm run dev`) plus the
  graph data layout (`data/graph_snapshot.json`, `graph_log.jsonl`,
  `findings.jsonl` via `--data-dir`/`COMPASS_DATA_DIR`).
- `/commit-push`: working tree clean, nothing to commit.
- Next (unchanged): replay (`?at=` odds scrub), wiring demo, auth gate.

## 2026-11-02 — Step 6 wiring (docs + interconnect demo)

Built `scripts/demo_interconnect.py`: drives the live stack for a
fixed topic slice (or the first discovered events), runs N news-team
cycles via `run_cycle` (rotating modes), then prints rev before/after,
new Hypothesis ids, and a `?at=<start>` scrub pair for the view slider.
`docs/live_test.md` gains the full living-loop wiring section (graph
:8090 → kalshi :8080 bare → 2 loops → view) plus the demo command.
Verified: script compiles, `--help` clean, suite 142/142 green.
Live end-to-end (services + loops + view) not run here.
Next: replay (`?at=` odds scrub), live wiring run, auth gate.

## 2026-11-02 — Wide cross-section seed

Live survey: events carry a native `category` field (Economics 56,
Sports 45, Elections 23, Politics 20, Science and Technology 18,
Entertainment 15, Financials 13, … in top 200 open).
`services/seed.py` gains `capture_cross_section` (bucket → native
categories, discovery order wins, `categories` metadata beside
`tickers`; `load` ignores it) + `parse_cross_section`; per-market
capture factored into `_capture_market_entry` (no behavior change).
`scripts/seed_kalshi.py` gains `--cross-section --per-category
--buckets` (`--event` explicit list still wins).
Captured `data/seed_wide.json`: 10 events × up to 3 markets = 22
tickers, 2 events in each of politics / economics / science /
culture / sports. `data/seed.json` untouched (POC pins it).
Verified: new tests + suite 144/144 green, wide seed loads offline
(22 tickers).

## 2026-11-03 — Events-first view (graph display fix)

Report: 3D view looked misleading — expected Event nodes linked by news,
saw a grey cloud. Grounded: live graph is 435 Markets / 50 Events /
22 Articles / 20 Subjects / 12 Hypotheses; no direct Event–Event
article edges exist (Article `about` Event + `mentions` Subject;
Event–Event only via derived red `shares-subject-with`, doubled
bidirectionally); node size = degree = market count; legend omitted
Happening/Audit/`challenges`.
Fix (chosen option): `view/src/graph.js` hides Markets by default,
keeps all Events (even unlinked), recomputes degree/size/cap on the
visible subgraph, pins Events first under the cap, reports hidden
count; `view/src/App.jsx` gains a `markets` toggle, hidden-count bar
text, full legend. `view/test/graph.test.mjs` (node:test stdlib) +
`npm test` script: 5/5 green; `npm run build` clean. Standalone
`demo/*.html` viewer untouched.
