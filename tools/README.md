# Tools

Service clients for the model-driven brain (`squad/brain.py`). Each
module builds a `{tool_name: {description, func}}` registry bound to a
live service base URL; `as_structured()` converts a registry into
LangChain `StructuredTool`s. The brain calls `func`s directly, so the
registry is plain data — usable with or without LangChain.

## Contract

- Every function returns a dict, never raises: `{ok: True, ...}` on
  success, `{ok: False, error: ...}` under each documented failure
  (bad args, unknown tool, unreachable service, unparseable model
  reply). Fail-soft on purpose — the model reads the error and decides
  what to do next.
- Timeouts are 120s for service-to-service HTTP (cold graph reads,
  sweeps); per-article fetch stays short in `articles.py`.

## Kalshi (`kalshi.py`, 5 tools)

Reads the Kalshi feed service (`services/kalshi_server.py`), syncs
its watchlist, triggers odds publishing.

| Tool | Does |
|---|---|
| `kalshi_discover_events` | Open events (tickers + titles) for topic choice |
| `kalshi_event_markets` | Open markets inside one event (tickers, titles, prices) |
| `kalshi_sync_watchlist` | Point the watchlist at event tickers (the loop's only write to Kalshi) |
| `kalshi_trigger_sweep` | Publish the watched book into the graph API (Scout nodes + moves) |
| `kalshi_service_status` | Health, watchlist size, publish wiring (no full ticker list) |

## Graph (`graph.py`, 5 tools)

Inspects the curated graph, writes only through the gate.

| Tool | Does |
|---|---|
| `graph_summary` | Revision + node/edge counts (cheap liveness check) |
| `graph_list_hypotheses` | Committed hypotheses with endpoint events, evidence counts, challenge flags |
| `graph_post_proposals` | Commit a JSON list of proposals (Skeptic + role lanes apply) |
| `graph_post_finding` | Append an ops note to the findings log |
| `graph_trigger_derive` | Recompute Connector derivations (`shares-subject-with`, `has-market`) |

## News (`news.py`, 2 tools)

Launches deterministic `news_loop` stage cycles in-process (no model
calls of their own — the spend stays in the brain + lane LLMs).

| Tool | Does |
|---|---|
| `news_run_cycle` | One cycle: discover → scan → pair → propose → commit. Narrow with `event_filter` / `mode_pin`, stride history with `history_snaps` / `history_hours` / `history_stride_hours` |
| `news_run_batch` | Several cycles concurrently (ThreadPool, default 4 workers) over distinct event sets; results in input order. Accepts `cycles` (list) or `cycles_json` (string) |

## Articles (`articles.py`, not a registry)

Shared fetch + text helpers imported directly by lanes, not called as
brain tools: `fetch_article` (URL → visible text, boilerplate tags
stripped, 8k chars), `summarize` (extractive, term overlap + position),
`llm_summarize` and `llm_relevant` (any LangChain chat model; raise on
failure so callers fall back to extractive, and relevance is
fail-open — anything but an explicit NO keeps the article).
