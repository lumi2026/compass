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
- Seed across categories:
  `uv run python scripts/seed_kalshi.py --event KXWARSHOUT-26JUL --event KXSPACEXMARS-30 --event KXTOPLLMGENERAL-30 --out data/seed_mvp.json`
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
- Kill the API mid-session: view shows a stale banner and recovers on restart.
- The as-of box filters by world time (`valid_at`); odds stay live until replay lands.

## LangSmith
- Key lives in `.env` as `LANGSMITH_API_KEY`. Tracing is opt-in: set `LANGSMITH_TRACING=true`. Runners print `langsmith=on project=...` (key never printed).
- Override: `LANGSMITH_PROJECT=demo-1 uv run python scripts/run_squad.py ...` or `--langsmith-project demo-1`.
