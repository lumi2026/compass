# Compass — original plan

Dumped from project memory (`compass.md`) on Day 3 (Oct 28, 2026).
Authoritative for "what did we set out to build"; progress is tracked in
`docs/progress.md`.

- **Vision**: Situation-aware AI consultant. Living Event Graph + Agent Squad.
  Proactive monitoring, not Q&A bot.
- **Demo sources**: Kalshi (prediction markets/events) + GDELT DOC 2.0
  (news/artlist JSON).
- **Stack**: Python backend in this repo. Monorepo, service-ready, 5 layers.
  Interfaces: SourcePlugin, LangChain model factory, storage interface.
  File-backed graph now. Seeded offline demo.
- **Squad**: Scout + Investigator + Connector fan-out, Skeptic as gate,
  Graph Update API (Validate|Commit|Record). Workspace: Cytoscape.js +
  human feedback loop to new tasks.
- **Seed**: `scripts/seed_kalshi.py` captures events+markets+books+candles to
  `data_local/seed.json`; service `--seed` serves it offline; `merge_state` never
  blanks seeded detail with empty polls.
- **Kalshi surface**: events, markets per event, orderbook (single+batch),
  candles (batch, 1/60/1440min). Far-future markets may have zero liquidity;
  smoke tolerates that.
