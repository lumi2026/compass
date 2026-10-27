# Compass Progress

## Kalshi service (goal: historical + live/websocket, topics, agent subscriptions)

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
