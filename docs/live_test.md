# Live test — squad split

## Baseline (must pass pre-change)
- `uv run python scripts/graph_poc.py` — derived Elon<->NATO link, re-run 0, save/load roundtrip.
- `uv run python scripts/smoke_news_pipeline.py --event KXSPACEXMARS-30 --max-articles 3` — exit 0, `/tmp/smoke_articles/` non-empty.
- `uv run python scripts/run_news_agent.py --max-articles 3` — 3 proposals; `engine=extractive` without key, `engine=llm` with `MODEL_API_KEY`.
- Always use `uv run python` (not system `python3`): only the venv has `dotenv`/`langsmith`, so system python silently skips `.env`, tracing, and LLM keys.

Known gaps: `KXTOPLLMGENERAL-30` has zero markets (expect conf floor, no crash). LLM path needs `MODEL_API_KEY` in `.env`.

## New: full squad
- Offline: `uv run python scripts/run_squad.py --event KXSPACEXMARS-30 --snapshot <fixture.gz> --seed data/seed.json --out /tmp/squad_graph.json`
- Live: `uv run python scripts/run_squad.py --event KXSPACEXMARS-30 --terms spacex starlink "elon musk"`
- The runner walks back up to `--lookback 8` snapshots (default) until term quotas fill, so thin newest snapshots don't blank the run. `--snapshot` pins one fixture file instead.
- Success: exit 0, `nodes=`/`edges=` printed, `followups:` list, out JSON loads, `refresh re-run == 0` on second derive (covered in tests).

## Suite
- `uv run python -m pytest tests/ -q` (offline fixtures; live scripts excluded).

## MVP (multi-event + viewer)
- Seed across categories (frozen 3-event set):
  `uv run python scripts/seed_kalshi.py --event KXWARSHOUT-26JUL --event KXSPACEXMARS-30 --event KXTOPLLMGENERAL-30 --out data/seed_mvp.json`
- Wide cross-section seed (politics, economics, science, culture, sports — 2 events each, native `category` field):
  `uv run python scripts/seed_kalshi.py --cross-section --per-category 2 --out data/seed_wide.json`
  (override buckets with `--buckets 'politics:Politics,Elections sports:Sports'`; `data/seed.json` stays the small POC seed)
- Live run (news scan + graph + page):
  `uv run python scripts/run_mvp.py --seed data/seed_mvp.json`
  (defaults write `demo/mvp_graph.json` + `demo/mvp_graph.html`)
- Open `demo/mvp_graph.html` in a browser (standalone Cytoscape page, no server).
- Offline check: `uv run python scripts/graph_poc.py --out /tmp/graph_poc.json` then render the HTML the same way `run_mvp.py` does.

## View (React 3D over live graph API)
- Terminal 1: `uv run python -m services.graph_server --port 8090`
- Terminal 2: `cd view && npm install && npm run dev`
- Open http://localhost:5173 in a browser.
- Expect counts + `rev` in the bar, click node for details + neighbours.
- Events-first: Market odds nodes and Subject match-logic nodes are
  hidden by default (bar shows both hidden counts); tick `markets` /
  `subjects` to reveal each layer. Events always stay, even unlinked;
  size/cap use the visible subgraph only.
- Hypotheses render as purple edges between their two events (title +
  evidence articles + audit flag on click), never as nodes; anything
  uncollapsible keeps its node so nothing is dropped.
- Kill the API mid-session: view shows a stale banner and recovers on restart.
- Bar: counts + `rev`, layer toggles, one controls hint (no legend —
  kinds show in details). +/− buttons zoom the camera, Reset refits
  it and clears the selection (scroll still zooms, right-drag
  rotates). The as-of input is removed; `GET /graph?at=` stays in
  the API for replay.
- Checks: `cd view && npm test` (transform + zoom math, stdlib only),
  `npm run build`.
- Event descriptions: clicking a blue Event shows category · series ·
  qualifier, the lead market + price, market/article/hypothesis counts,
  and top article titles; hover shows category + lead price. Meta flows
  from Kalshi (`auto_topics` keeps `event_meta`, Scout Event proposals
  carry it as props); existing graphs catch up via
  `uv run python scripts/backfill_event_meta.py` (idempotent —
  duplicates merge to `refreshed`).
- Pruning dead markets: `uv run python scripts/prune_dead.py` (dry run)
  then `--apply`. Retires closed/settled markets as one logged batch
  (`commit_retire`, replay-safe); events are never retired. Stop the
  graph API first — the script refuses `--apply` while `:8090`
  answers. Kalshi throttles burst queries (429): per-event failures
  keep their markets, so a throttled run retires nothing.

## LangSmith
- Key lives in `.env` as `LANGSMITH_API_KEY`. Tracing is opt-in: set `LANGSMITH_TRACING=true`. Runners print `langsmith=on project=...` (key never printed).
- Override: `LANGSMITH_PROJECT=demo-1 uv run python scripts/run_squad.py ...` or `--langsmith-project demo-1`.

## Wiring (full living loop)

Start order matters: graph first, then kalshi, then the loops, then the view.

- Terminal 1 (graph API, owns the store + `data/graph_*`):
  `uv run python -m services.graph_server --port 8090`
- Terminal 2 (kalshi service, no seed needed for discovery; `--graph-url`
  enables odds publishing, `--sweep` primes the graph once at startup):
  `uv run python scripts/kalshi_service.py --port 8080 --graph-url http://127.0.0.1:8090`
  (add `--sweep` once the watchlist is non-empty; without `--event`/
  `--ticker`/`--seed` the startup watchlist is empty, so let the news
  loop sync it first or pass `--sweep` after a manual `POST /topics`.)
- Terminal 3 (news loop A, one slice):
  `uv run python scripts/news_loop.py --graph-url http://127.0.0.1:8090 --kalshi-url http://127.0.0.1:8080 --loop --interval-min 15`
  (no `--event`: topics start from the graph's standing set, then up
  to `--event-limit` newcomers; newcomers get Event nodes posted.
  Each cycle is five deterministic subagent stages — discover, scan,
  pair, propose, commit — dispatchable individually via
  `run_stage(name, ctx)` for a future orchestrator; `run_cycle` is
  the reference dispatcher.)
- Terminal 4 (news loop B, a different `--event` slice, same graph):
  `uv run python scripts/news_loop.py --graph-url http://127.0.0.1:8090 --kalshi-url http://127.0.0.1:8080 --event KXSPACEXMARS-30 --loop --interval-min 15`
- Terminal 5 (view):
  `cd view && npm install && npm run dev` — open http://localhost:5173
- Terminal 6 (audit loop — only unaudited hypotheses, keeps recorded
  to `data/audit_state.json`, never re-audited, nothing deleted):
  `uv run python scripts/audit_loop.py --loop --interval-min 60`
  (`--once` for one pass, `--dry-run` to print verdicts without
  posting, `--model None` to keep everything without LLM calls)

The loop only writes Kalshi via `sync_watchlist` (`POST /topics`); the Kalshi terminal stays quiet by design (`log_message` silenced). Kalshi→graph odds publishing is enabled by `--graph-url` at startup (add `--sweep` to prime once), or at runtime with `POST /publish/graph` + `POST /publish/sweep`.

Checks: `GET /health` on both services; loop stdout stage logs (`topics/terms → hits → pairs → posted/rev/followups`); view bar shows counts + `rev` and updates within one poll (5s).

## Interconnect demo (seed 3 topics, run team, scrub time)

- `uv run python scripts/demo_interconnect.py --event KXWARSHOUT-26JUL --event KXSPACEXMARS-30 --event KXGDPQ3-25 --cycles 2`
- With no `--event` flags it discovers the first 3 events from the kalshi service instead.
- The script runs that many news-team cycles (rotating modes) against the live graph API, then prints rev before/after, the new Hypothesis ids, and a `?at=<start>` scrub pair.
- In the view, drag the as-of slider from the printed start timestamp to now to watch the interconnect arrive. Odds stay live on older frames until replay lands.
- Pass `--model None` for a free offline-LLM run (extractive hypotheses).
