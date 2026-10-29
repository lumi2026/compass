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

## LangSmith
- Key lives in `.env` as `LANGSMITH_API_KEY`. Tracing is opt-in: set `LANGSMITH_TRACING=true`. Runners print `langsmith=on project=...` (key never printed).
- Override: `LANGSMITH_PROJECT=demo-1 uv run python scripts/run_squad.py ...` or `--langsmith-project demo-1`.
