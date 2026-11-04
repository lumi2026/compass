# Services

Two long-running HTTP services plus the plumbing between them. Everything
here is local-only (`127.0.0.1`) and stdlib-first, except the graph API,
which uses FastAPI + Pydantic.

| Module | Purpose | Key names |
|---|---|---|
| `kalshi_auth.py` | Kalshi request signing + credential loading | `Credentials`, `load_credentials`, `auth_headers`, `sign_request` |
| `kalshi_listener.py` | Live Kalshi websocket (`ticker`, `orderbook_delta`, `trade`) with REST-poll fallback | `KalshiListener`, `handle_message`, `TickerState`, `Update` |
| `feed_store.py` | Thread-safe hub: latest state, per-ticker history, SSE bus, webhooks | `FeedStore` |
| `feed_seed.py` | Freeze live data to JSON / restore it offline | `capture`, `capture_events`, `capture_cross_section`, `load`, `save` |
| `kalshi_server.py` | The Kalshi feed HTTP service (wires listener + store together) | `Service`, `serve`, `make_handler` |
| `graph_api.py` | The Living Event Graph HTTP API (curated store, gated writes) | `GraphService`, `create_app` |

`__init__.py` re-exports the two shared types: `FeedStore`, `KalshiListener`.

## Kalshi feed service (`:8080`)

Reads Kalshi (live or frozen) and fans updates out to agents.

```sh
uv run python scripts/kalshi_service.py --port 8080
```

Auth comes from the environment only — `KALSHI_KEY_ID` plus
`KALSHI_PRIVATE_KEY` (or `KALSHI_PRIVATE_KEY_PATH`), loaded via
`load_dotenv`. No keys means poll mode: the socket handshake needs auth
even for public channels, so the listener falls back to REST orderbook
polling. `--env demo|prod` picks the endpoints; `--seed <file>` serves
frozen data with no network.

Flow: `KalshiListener` turns socket messages into `Update`s (pure
`handle_message`, no network — that is what the tests cover), `Service`
merges them into the `FeedStore`, and agents read two ways: polling
(`GET /snapshot`, `GET /history`) or push (`GET /stream` SSE,
`POST /subscriptions` webhooks). `merge_state` never blanks seeded
detail with empty polls; a dead webhook URL is auto-discarded.

| Endpoint | Use |
|---|---|
| `GET /health` | Liveness: socket mode, watchlist size, publish config |
| `POST /topics` | Set watchlist: `{events, market_tickers}` or `{auto, top_k, scan_limit}` |
| `GET /events`, `GET /markets?event=` | Discovery for the news loop |
| `GET /snapshot`, `GET /history`, `GET /stream` | Poll latest / candles / SSE push |
| `POST /publish/graph`, `POST /publish/sweep` | Feed odds into the graph API (Scout nodes + `market-move` happenings, first sighting is a baseline, never a move) |

## Graph API (`:8090`)

Owns the curated graph: one in-memory store, every mutation serialized
through a lock, atomic snapshot writes plus an append-only commit log.

```sh
uv run python -m services.graph_api --port 8090
```

State lives in `--data-dir` (`COMPASS_DATA_DIR`, default
`data_local/`): `graph_snapshot.json`, `graph_log.jsonl`,
`findings.jsonl`. The log is the audit trail and the odds history —
edit a file and you fork from every other writer, so always go through
the API.

| Endpoint | Use |
|---|---|
| `GET /health` | `{ok, nodes, edges, revision}` |
| `GET /graph`, `GET /graph?at=<ts>` | Full payload, or time travel (`valid_at` filter; odds stay live until replay lands) |
| `POST /proposals` | Gated writes: per-proposal verdicts, role-lane + confidence checks |
| `POST /retire`, `POST /findings` | Remove nodes / append raw ungated findings |

Poll with `If-None-Match: <revision>` for a cheap `304` when nothing
changed. One machine, one writer, no auth yet — the pre-public token
gate is a tracked deferral, so keep this on localhost.

## Conventions

- Every module states its contract in the docstring first line. Tests
  assert the contract, not the implementation.
- Upstream failures are fail-soft and logged, never raised into the
  serve path: per-event discovery skips, graph-down publish is log-only,
  throttled prunes retire nothing.
- Timeouts: service-to-service HTTP calls allow 120s; per-article fetch
  and per-tick publish stay short.
