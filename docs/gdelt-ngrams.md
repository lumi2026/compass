# GDELT Web News NGrams 3.0 — integration notes

Source: <https://blog.gdeltproject.org/announcing-the-new-web-news-ngrams-3-0-dataset/>
Measured Oct 28, 2026 (Day 2 probe).

## What it is

Per-snapshot unigram dump of monitored global news (152 languages). Each record
= one word occurrence (space-segmented langs) or one grapheme cluster
(scriptio continua), with context snippets and the article URL.

Record fields: `date`, `ngram` (with surrounding punctuation), `lang` (CLD2),
`type` (1 = word, 2 = character), `pos` (article decile 0–90; ≤20 ≈ lead =
high relevance), `pre` / `post` (~7-word snippets), `url`.

Longer phrases via snippet rules: bigram = ngram + start-of-post; trigram =
middle word as ngram, neighbors at end-of-pre / start-of-post.

## Measured access (not as documented)

- URL: `https://data.gdeltproject.org/gdeltv3/webngrams/YYYYMMDDHHMMSS.webngrams.json.gz`
- NOT every minute: files land ~every 15 min at :01, :16, :31, :46
  (e.g. `20261027010100` OK; `:00`/`:05` 404). Matches GDELT's 15-min cycle.
- Size: ~23–51 MB gzip per snapshot, ~1.25M records (Oct 2026). Downloads in
  ~1–2s — static host is NOT throttled (unlike the DOC API).
- History back to 2020-01-01 verified; BigQuery mirror `gdelt-bq.gdeltv2.webngrams`
  exists but unused (no GCP here).
- Caveats from the post: elevated duplicate URLs (dedupe by url+ngram);
  strip punctuation for matching.

## Integration design (proposed)

New source plugin `sources/ngrams.py`:

1. **Fetch**: resolve latest snapshot (try :01/:16/:31/:46 walking back ≤30
   min), stream-download, parse gzip line-by-line, filter on the fly —
   never hold 1.25M rows. ~10–20s CPU per snapshot in background.
2. **Match**: watch-term list (Kalshi-linked: candidate names, "rate cut",
   "tariff", …). Casefold + strip punctuation. Optional bigram via
   pre/post rules. Keep `lang == en` (+ translations later).
3. **Two outputs**:
   - a. **Burst signal**: per-term mention counts per snapshot → tiny time
     series (`data_local/ngram_counts.json`). Spike = candidate explanation for a
     Kalshi price move; complements `timelinevolraw` without touching the
     throttled API.
   - b. **Provenance**: matched records → `{url, pre, ngram, post, pos}` —
     feeds `data_local/news_digest.json` topics and agent context. `pos <= 20`
     ranks lead-paragraph hits first.
4. **Retention**: discard raw snapshots (31 MB × 96/day too heavy); keep only
   matched lines + counts.
5. **Scheduling**: same 15-min background loop as `scripts/collect_news.py`
   (share the gap/backoff discipline; static host needs none observed).

Why this over DOC polling: no 429s, full-text term coverage (not just
relevance-ranked top-N articles), per-minute-ish granularity, free context
snippets for relevancy filtering without fetching articles.
