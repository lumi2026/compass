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

## Day 2 — Oct 28 (today)

### Plan
- News service baseline: behavior, latency, today's USA sample, how we use it later.

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

## Day 3 — Oct 28 (hackathon day 3)

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
  overrides unchanged. Tests updated; run pending approval.
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
- Model switch: `COMPASS_MODEL=muse-spark-1.3-contributor` smoke OK live
  (`OK` via api.meta.ai/v1). Swap = one env line, no code change.
- Per-agent models: `make_chat_model(provider, model)` + new
  `resolve_model(spec)` (auto|None|model-id|{provider,model}|instance);
  `NewsAgent.run` resolves `task["model"]` over agent default; engine line
  now tags the model id (`engine=llm:<id>`). 18/18 tests green.
- Subject-level relevance: filter keeps articles substantively about a key
  subject without requiring they answer the question (SpaceX news counts
  for a Mars question); live re-run pending.

### Start-of-day review (plan vs progress)
- Original plan (`docs/plan.md`): situation-aware consultant, Living Event
  Graph + Squad (Scout/Investigator/Connector fan-out, Skeptic gate, Graph
  Update API), Kalshi + GDELT DOC 2.0 sources, service-ready backend, seeded
  offline demo, Cytoscape workspace + human feedback.
- Day 1 fit: Kalshi source + service + seed — on-plan (backend + offline demo).
- Day 2 fit: GDELT baseline + NGrams 3.0 pivot (DOC v2 throttled, static host
  fast — justified extension, not drift), single news-agent prototype with
  Muse Spark via LangChain — on-plan toward Squad/Investigator.
- Gaps to close for demo: Event Graph + Graph Update API, full
  Scout/Investigator/Connector/Skeptic split, Cytoscape workspace + feedback
  loop. Accumulator DB today is the bridge (shared space agents query).

### Meta API wiring (Muse Spark via LangChain)
- `squad/news_agent.py`: new `llm_relevant` tool (YES/NO + reason,
  fail-open) + wired into `NewsAgent.run` before summarization;
  detail gains `relevant_filter=dropped N of M`.
- `tests/test_news_agent.py`: +3 tests (NO filters, unparseable keeps,
  agent drops irrelevant); 11/11 green.
- Live run blocked: no `MODEL_API_KEY` in `.env` (only Kalshi keys present).
  Next: user adds `MODEL_API_KEY=...` then
  `uv run python scripts/run_news_agent.py --max-articles 3`.
- Provider-swap review: `squad/models.py` factory generalized —
  `MODEL_PROVIDER` (meta|openai|deepseek|custom) + presets, generic or
  provider keys, `COMPASS_MODEL`/`MODEL_BASE_URL` overrides; agent tools
  already provider-agnostic (`model.invoke` only). 15/15 tests green.
