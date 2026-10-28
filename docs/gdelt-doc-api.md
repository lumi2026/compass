# GDELT DOC 2.0 API — reference notes

Source: <https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>
Rolling window: ~1.5 years of coverage (was 3 months at debut).

Base URL: `https://api.gdeltproject.org/api/v2/doc/doc`

## Request parameters (URL params)

| Param | Values | Notes |
|---|---|---|
| `query` | keywords, `"exact phrases"`, `(a OR b)`, operators below | All terms ANDed. Operators must be lowercase, space-separated, inside `query`. |
| `mode` | `artlist`, `timelinevol`, `timelinevolraw`, `timelinevolinfo`, `timelinetone`, `timelinelang`, `timelinesourcecountry`, `tonechart`, `artgallery`, image modes | We use `artlist`. Timelines are the intensity/tone baseline signal. |
| `format` | `html` (default), `json`, `jsonp`, `csv`, `rss`/`rssarchive` (artlist only), `jsonfeed` (artlist only) | We use `json`. CORS `*`. CSV is UTF-8 with BOM. |
| `timespan` | `<n>min`, `<n>h`/`hours`, `<n>d`/`days`, `<n>w`/`weeks`, `<n>m`/`months`; min 15 min | Offset backwards from now. Default = whole window. |
| `startdatetime` / `enddatetime` | `YYYYMMDDHHMMSS` | Exact window instead of offset. Use for gap-free polling. |
| `maxrecords` | 1–250, default 75 | Artlist / image-collage modes only. |
| `sort` | `hybridrel` (default), `datedesc`, `dateasc`, `tonedesc`, `toneasc` | Default is relevance + outlet popularity, NOT chronological. Use `datedesc` for newest-first. |
| `timelinesmooth` | 1–30 | Moving-average smoothing for timeline modes (shifts peaks right). |
| `timezoom` | `yes` | HTML timelines only; interactive zoom. |

## Query operators (inside `query`)

- `"donald trump"` — exact phrase. `(clinton OR sanders OR trump)` — OR block, no nesting.
- `-sourcelang:spanish` — minus excludes any term.
- `domain:cnn.com` — outlet match (subdomains included); `domainis:un.org` — exact match.
- `sourcecountry:` — outlet country; spaceless name (`sourcecountry:saudiarabia`) or 2-char FIPS (`sourcecountry:US`). USA-today = `sourcecountry:US`.
- `sourcelang:spanish` (or 3-letter code) — original-language filter; search terms still match English translations.
- `theme:TERROR` — GKG theme (see LOOKUP-GKGTHEMES.TXT).
- `tone>5`, `tone<-5`, `toneabs>10` — tone filter (−100..+100, typical −10..+10).
- `near20:"trump putin"` — proximity; `repeat3:"trump"` — minimum term frequency (topic-ness filter).
- Image-only ops (image modes): `imagetag:"..."`, `imagewebtag:"..."`, `imageocrmeta:"..."`, `imagewebcount>100`, `imagenumfaces>3`, `imagefacetone<-1.5`.

## Modes detail

- `artlist` — article list (title, URL, source country, language, date, social image). Only mode with RSS/JSONFeed.
- `timelinevol` — matches as % of total global volume per step (15-min steps < 72h, hourly < 1 week, daily above). `timelinevolraw` — raw counts + `norm` field (total monitored). `timelinevolinfo` — plus top-10 articles per step (spike triage).
- `timelinetone` — average tone per step. `tonechart` — tone histogram (−100..+100).
- `timelinelang` / `timelinesourcecountry` — volume broken down by language / outlet country.

## Compass usage

- Today's USA news: `query=sourcecountry:US`, `mode=artlist`, `timespan=1days`, `sort=datedesc`, `format=json`.
- Baseline intensity: same query with `mode=timelinevolraw`, `timespan=1days`.
- Poll cadence: 15 min (GDELT update resolution). Use `startdatetime`/`enddatetime` windows; expect ~10–33s latency per request and aggressive 429s (max 1 req / 5s per client; shared egress IPs get throttled harder — consecutive successes needed minutes of spacing on Oct 28) — background thread, cache, never block request path.
- Dedupe by URL hash (already in `sources/gdelt.py` `normalize`).
- Measured Oct 28 (Day 2 probe): `sourcecountry:US` artlist + `datedesc` returned 10 rows, all `seendate` 20261027T051500Z (~19.5h stale; v1 agreed, `lastminutes:360` empty). `timelinevolraw` unobtainable (429 even after 5-min cooldown) — stall vs frozen shard undetermined, recheck later. `sourcecountry:US` alone is noisy (ads, local filler) — add `theme:`/`repeat:`/tone/domain filters for a usable feed.
- Background watcher: `scripts/collect_news.py` (`DEFAULT_TOPICS`) writes `data/news_digest.json` for agents; `--once` or `--loop --interval-min 15`, `--gap-sec` spacing between topics (default 240s).

## Related (legacy)

- v1 full-text search (`api/v1/search_ftxtsearch/...`, last-24h only, 15–30 min rolling delay): alive but index observed ~19h stale Oct 2026 — fallback only. Notes in `progress.md` Day 2.
- Event DB codebook (GDELT 2.0 events/mentions tables, bulk TSV): `QuadClass`, `GoldsteinScale`, `ActionGeo_*`, per-mention `Confidence` — for later event-level aggregation, not the live feed.
