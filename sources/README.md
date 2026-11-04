# Sources (Layer 1)

Upstream data adapters. Each module talks to one outside API and hands
the rest of Compass normalized Python objects — never raw JSON. The
orchestrator and agents only depend on the contracts in `base.py`, so
adding a feed means adding a plugin, not rewiring callers.

| Module | Upstream | Yields |
|---|---|---|
| `base.py` | — (contracts) | `MarketEvent`, `NewsArticle`, `SourcePlugin` protocol |
| `kalshi.py` | Kalshi prediction-market REST | events, markets, order books, candles |
| `gdelt.py` | GDELT DOC 2.0 (`artlist`) | news articles |
| `ngrams.py` | GDELT NGrams 3.0 static snapshots | term burst counts + article samples |

## Contracts (`base.py`)

- `SourcePlugin`: `name`, `fetch_raw(limit)`, `normalize(raw)`,
  `fetch_normalized(limit)`. `KalshiSource` and `GdeltSource` implement
  it; `ngrams.py` deliberately does not (see below).
- `MarketEvent(source, external_id, title, raw)`: a Kalshi event. The
  raw payload rides along for display metadata.
- `NewsArticle(source, external_id, title, url, published_at, raw)`:
  one news item, whatever the upstream.

## Kalshi (`kalshi.py`)

Public REST, no auth needed for market data. Covers the full surface
the graph consumes: `fetch_raw`/`fetch_normalized` (open events),
`fetch_event` (one event + display meta), `fetch_markets`,
`fetch_orderbook(s)`, `fetch_candlesticks`. Batch endpoints are
preferred so the squad pulls many tickers per request.

Watch the money parsing: Kalshi returns prices as `*_dollars` strings
(`"0.5400"`) and volumes as `*_fp` strings. Everything is converted to
integer cents / integer contracts at the boundary (`_to_cents`,
`_to_contracts`), and every normalizer accepts both shapes, so seeds,
live REST, and socket payloads all parse. Retries back off only on
429; anything else raises to the caller, which decides fail-soft.

```python
from sources.kalshi import KalshiSource
src = KalshiSource()
events = src.fetch_normalized(limit=5)
books = src.fetch_orderbooks(["KXFOO-25-BAR"], depth=10)
```

## GDELT DOC 2.0 (`gdelt.py`)

`GdeltSource(query, timespan, sort)` runs public `artlist` queries and
returns `NewsArticle`s (`published_at` = upstream `seendate`, id =
URL hash). The hard limit is upstream: max ~1 request per 5s, throttled
with 429s — retries back off 7s, 14s, 21s, and successes can still take
minutes of spacing on a busy network. Treat DOC as the slow,
high-signal source: 15-minute polls over `startdatetime`/`enddatetime`
windows, dedupe by URL hash, track max `seendate` lag as the freshness
metric.

## NGrams 3.0 (`ngrams.py`)

The fast source. GDELT publishes full snapshots every 15 minutes
(`:01/:16/:31/:46`) on an unthrottled static host (~25–50 MB each, back
to 2020). Helpers: `resolve_snapshot` (newest downloadable, skipping
the unlanded freshest mark), `snapshot_candidates` / `select_snapshots`
/ `backfill_snapshots` (bounded history walks, ~13 files per 48h),
`ngram_mentions` (streaming count + URL/sample hits per term, never
loads whole rows), `articles_from_mentions` (samples → `NewsArticle`s),
`mentions_with_lookback` (newest-first walk until quotas fill).

Two things to know. First, this module is helpers, not a
`SourcePlugin`: callers drive the scan and convert hits themselves.
Second, matching follows the NGrams 3.0 phrase rule — the ngram is the
lower-middle word ("white house" matches on "house" with "white" as the
tail of `pre`), verified in pre/post context. Unigrams are exact
matches and noisy ("federal" pulls Australian politics); prefer
2–5-word phrases, which the matcher handles length-generically.

```python
from sources import ngrams as ng
ts, path = ng.resolve_snapshot(dest_dir="/tmp")
hits = ng.ngram_mentions(["white house", "interest rates"], path)
arts = ng.articles_from_mentions(hits, ts)
```

## Conventions

- Stdlib HTTP only (`urllib`), one `User-Agent`, per-module timeouts
  (20s APIs, 120s snapshot downloads).
- Parse at the boundary, fail-soft above: normalizers skip bad rows,
  callers catch per-event/per-ticker failures so one delisted market
  never blocks a sweep.
- No keys, no state, no disk writes here (snapshot caching to `/tmp`
  is the caller's choice). Anything needing credentials lives in
  `services/kalshi_auth.py`, not in this folder.
