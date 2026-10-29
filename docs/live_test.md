# Live test — squad split

## Baseline (must pass pre-change)
- `python3 scripts/graph_poc.py` — derived Elon<->NATO link, re-run 0, save/load roundtrip.
- `python3 scripts/smoke_news_pipeline.py --event KXSPACEXMARS-30 --max-articles 3` — exit 0, `/tmp/smoke_articles/` non-empty.
- `python3 scripts/run_news_agent.py --max-articles 3` — 3 proposals; `engine=extractive` without key, `engine=llm` with `MODEL_API_KEY`.

Known gaps: `KXTOPLLMGENERAL-30` has zero markets (expect conf floor, no crash). LLM path needs `MODEL_API_KEY` in `.env`.

## New: full squad
- Offline: `python3 scripts/run_squad.py --event KXSPACEXMARS-30 --snapshot <fixture.gz> --seed data/seed.json --out /tmp/squad_graph.json`
- Live: `python3 scripts/run_squad.py --event KXSPACEXMARS-30 --terms spacex starlink "elon musk"`
- Success: exit 0, `nodes=`/`edges=` printed, `followups:` list, out JSON loads, `refresh re-run == 0` on second derive (covered in tests).

## Suite
- `uv run python -m pytest tests/ -q` (offline fixtures; live scripts excluded).

## LangSmith
- Key lives in `.env` as `LANGSMITH_API_KEY`. Tracing is opt-in: set `LANGSMITH_TRACING=true`. Runners print `langsmith=on project=...` (key never printed).
- Override: `LANGSMITH_PROJECT=demo-1 python3 scripts/run_squad.py ...` or `--langsmith-project demo-1`.
