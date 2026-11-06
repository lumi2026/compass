"""GDELT NGrams 3.0 snapshot helpers: resolve, download, match, count.

Snapshots land every 15 min (:01/:16/:31/:46) on the static host, which
is fast and unthrottled (unlike DOC 2.0). This module streams one
snapshot file and matches caller watch-terms with the NGrams 3.0 phrase
rules; raw snapshots are the caller's to keep or discard.
"""

import datetime
import gzip
import json
import os
import re
import string
import time
import urllib.request
from urllib.parse import urlsplit

from .base import NewsArticle

SNAP_BASE = "https://data.gdeltproject.org/gdeltv3/webngrams"
UA = {"User-Agent": "compass/0.1"}
STRIP = str.maketrans("", "", string.punctuation)


def snapshot_candidates(now_ts: float, lookback_min: int = 120,
                      delay_min: int = 10) -> list[str]:
    """Newest-first snapshot timestamps (YYYYMMDDHHMMSS).

    Starts `delay_min` back because the freshest mark hasn't landed yet
    (requesting it is a guaranteed 404). The resolve loop below remains
    as the backstop for slower-than-usual publishes.
    """
    now = datetime.datetime.fromtimestamp(
        now_ts, tz=datetime.timezone.utc
    ).replace(second=0, microsecond=0)
    out, seen = [], set()
    t = now - datetime.timedelta(minutes=delay_min)
    while len(out) < lookback_min // 15 + 1:
        mm = t.minute
        mark = "46" if mm >= 46 else "31" if mm >= 31 else "16" if mm >= 16 else "01"
        ts = t.strftime("%Y%m%d%H") + mark + "00"
        if ts not in seen:
            seen.add(ts)
            out.append(ts)
        t -= datetime.timedelta(minutes=15)
    return out


def download_snapshot(ts: str, dest: str) -> str:
    """Fetch one webngrams snapshot file to dest. Returns dest."""
    req = urllib.request.Request(f"{SNAP_BASE}/{ts}.webngrams.json.gz", headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest


def resolve_snapshot(dest_dir: str = "/tmp") -> tuple[str, str]:
    """Newest downloadable webngrams snapshot. Returns (ts, path).

    Skips unlanded/slow marks silently (single shared helper, so no
    caller hand-rolls the miss loop). Reuses a cached file when present.
    """
    for ts in snapshot_candidates(time.time()):
        dest = os.path.join(dest_dir, f"ngram_{ts}.json.gz")
        if os.path.exists(dest):
            return ts, dest
        try:
            return ts, download_snapshot(ts, dest)
        except Exception:
            continue
    raise RuntimeError("no downloadable webngrams snapshot")


def select_snapshots(candidates: list[str], every_nth: int = 16) -> list[str]:
    """Oldest-first stride sample across candidate timestamps.

    `every_nth=16` over 15-min marks samples ~every 4h; the newest mark
    is always included so the tail stays fresh. Pure (testable).
    """
    ordered = sorted(set(candidates))
    if not ordered or every_nth <= 1:
        return ordered
    sampled = ordered[::every_nth]
    if sampled[-1] != ordered[-1]:
        sampled.append(ordered[-1])
    return sampled


def backfill_snapshots(hours_back: float = 48, every_nth: int = 16,
                       dest_dir: str = "/tmp",
                       now_ts: float | None = None) -> list[tuple[str, str]]:
    """Sampled (ts, path) pairs across history, oldest first.

    Bounded cost by construction: 48h at every-16th mark ≈ 13 files.
    Misses and unlanded marks are skipped; cached files reused.
    """
    cands = snapshot_candidates(time.time() if now_ts is None else now_ts,
                                lookback_min=int(hours_back * 60))
    out: list[tuple[str, str]] = []
    for ts in select_snapshots(cands, every_nth):
        dest = os.path.join(dest_dir, f"ngram_{ts}.json.gz")
        if os.path.exists(dest):
            out.append((ts, dest))
            continue
        try:
            out.append((ts, download_snapshot(ts, dest)))
        except Exception:
            continue
    return out


def _to_seendate(value, fallback=""):
    """Normalize a record/snapshot stamp to digest seendate format."""
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.datetime.strptime(value, fmt).strftime(
                "%Y%m%dT%H%M%SZ")
        except (ValueError, TypeError):
            continue
    return fallback


def articles_from_mentions(mentions, snapshot_ts):
    """Mention samples -> NewsArticles (lead hits first).

    Snippet text stands in for the title; the record date (or snapshot
    stamp) for seendate; domain parsed from the URL. Deduped by URL.
    """
    snap_seendate = _to_seendate(snapshot_ts)
    arts, seen = [], set()
    for term, m in mentions.items():
        for s in sorted(m["samples"], key=lambda r: r.get("pos", 99)):
            url = s.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            snippet = f"...{s.get('pre', '')} [{s.get('ngram', '')}] " \
                f"{s.get('post', '')}...".strip()
            arts.append(NewsArticle(
                source="ngrams",
                external_id=url,
                title=snippet,
                url=url,
                published_at=_to_seendate(s.get("date"), snap_seendate),
                raw={"domain": urlsplit(url).netloc, "term": term,
                     "pos": s.get("pos")},
            ))
    return arts


def _words(s: str) -> list[str]:
    return [w.translate(STRIP).lower() for w in s.split() if w.translate(STRIP)]


def _phrase_mid(phrase: list[str]) -> int:
    return (len(phrase) - 1) // 2  # bigram: ngram is the FIRST word (doc rule)


def _tail_ok(r: dict, phrase: list[str], mid: int) -> bool:
    """Pre/post context check, assuming the ngram already equals phrase[mid]."""
    pre, post = _words(r.get("pre", "")), _words(r.get("post", ""))
    before, after = phrase[:mid], phrase[mid + 1:]
    return pre[len(pre) - len(before):] == before and post[: len(after)] == after


def match_record(r: dict, phrase: list[str]) -> bool:
    """Multi-gram match per the NGrams 3.0 doc: the middle word of the
    phrase is the ngram, words before it are the tail of pre, words
    after it are the head of post. Unigram = exact ngram match."""
    if r.get("type") != 1 or not phrase:
        return False
    mid = _phrase_mid(phrase)
    if r.get("ngram", "").translate(STRIP).lower() != phrase[mid]:
        return False
    return _tail_ok(r, phrase, mid)


def ngram_mentions(
    terms: list[str],
    snapshot_gz: str,
    lang: str = "en",
    per_term_cap: int = 5,
) -> dict:
    """Count + sample uni/multi-gram hits per term in a snapshot file.

    Streams the gzip (never loads all rows). Phrases use pre/post rules.
    Returns {term: {"count": int, "urls": [...], "samples": [records]}}.

    Terms are grouped by their middle word so each record pays one
    ngram normalization + dict lookup instead of one full match per
    term — identical results, ~60x less work on wide term sets.
    """
    want = [(t.lower(), t.lower().split()) for t in terms]
    by_mid: dict[str, list[tuple[str, list[str]]]] = {}
    for t, phrase in want:
        if phrase:
            by_mid.setdefault(phrase[_phrase_mid(phrase)], []).append((t, phrase))
    counts: dict[str, int] = {t: 0 for t, _ in want}
    urls: dict[str, set] = {t: set() for t, _ in want}
    samples: dict[str, list] = {t: [] for t, _ in want}
    with gzip.open(snapshot_gz, "rt") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("lang") != lang or r.get("type") != 1:
                continue
            group = by_mid.get(r.get("ngram", "").translate(STRIP).lower())
            if not group:
                continue
            for t, phrase in group:
                if _tail_ok(r, phrase, _phrase_mid(phrase)):
                    counts[t] += 1
                    urls[t].add(r.get("url", ""))
                    if len(samples[t]) < per_term_cap:
                        samples[t].append(r)
    return {
        t: {"count": counts[t], "urls": sorted(urls[t]), "samples": samples[t]}
        for t, _ in want
    }


def mentions_with_lookback(terms: list[str], dest_dir: str = "/tmp",
                           max_snapshots: int = 8, per_term_cap: int = 20,
                           start_ts: float | None = None,
                           candidates: list[str] | None = None) -> tuple[dict, list]:
    """Newest-first walk over snapshots until quotas fill or budget spent.

    Thin/empty recent snapshots contribute nothing; the walk keeps going
    back. `candidates` overrides the time-derived list (tests). Returns
    (mentions, used) with used as [(ts, path)] newest-first. Raises
    RuntimeError when no snapshot could be read at all.
    """
    cands = (candidates if candidates is not None
             else snapshot_candidates(time.time() if start_ts is None
                                      else start_ts))
    counts: dict[str, int] = {}
    urls: dict[str, set] = {}
    samples: dict[str, list] = {}
    used: list[tuple[str, str]] = []
    for ts in cands:
        if len(used) >= max_snapshots:
            break
        dest = os.path.join(dest_dir, f"ngram_{ts}.json.gz")
        if not os.path.exists(dest):
            try:
                download_snapshot(ts, dest)
            except Exception:
                continue
        try:
            m = ngram_mentions(terms, dest, per_term_cap=per_term_cap)
        except Exception:
            continue
        used.append((ts, dest))
        for t, mm in m.items():
            counts[t] = counts.get(t, 0) + mm["count"]
            urls.setdefault(t, set()).update(mm["urls"])
            have = samples.setdefault(t, [])
            have.extend(mm["samples"][: max(0, per_term_cap - len(have))])
        if samples and all(len(samples[t]) >= per_term_cap for t in samples):
            break
    if not used:
        raise RuntimeError("no readable webngrams snapshot")
    return ({t: {"count": counts.get(t, 0), "urls": sorted(urls.get(t, set())),
                 "samples": samples.get(t, [])} for t in counts}, used)


STOP = set("""will the a an of to in on for who what when where be is are was
were by before after than more less most its it this that with from and or as
at end over under out up how many much his her their he she they we you have
has had do does did going get win take say says said new next first last high
low top no not vs via per day week month year time times will be des les der
die und""".split())


def extract_terms(title: str, max_terms: int = 5,
                  max_unigrams: int = 4) -> list[str]:
    """Title -> search terms: ordered bigrams, then proper-noun unigrams.

    Bigrams lead (the old first-N contract, widened): multi-word phrases
    are what the NGrams 3.0 pre/post rules match best. Proper-noun
    unigrams follow so the country/office-holder token survives long
    titles whose tail used to be cut ("2030 Brazilian presidential
    election" lost "brazilian" past the old cap): a token qualifies when
    capitalized in the title (short ALLCAPS acronyms like GDP count),
    significant, non-numeric, and non-generic. Single-token titles keep
    the old longest-token fallback.
    """
    from graph import GENERIC_TOKENS

    toks = [w for w in re.sub(r"[^a-z0-9 ]", " ", title.lower()).split()
            if w not in STOP and len(w) > 2]
    bigrams = [f"{a} {b}" for a, b in zip(toks, toks[1:])][:max_terms]
    if not bigrams and toks:
        longest = max(toks, key=len)
        if len(longest) >= 4:
            return [longest]
        return []
    proper = set()
    for w in re.findall(r"[A-Za-z0-9]+", title):
        low = w.lower()
        if not w[0].isupper() or low in STOP or len(low) <= 2:
            continue
        if low.isdigit() or low in GENERIC_TOKENS:
            continue
        if w.isupper() and len(w) > 5:
            continue  # long ALLCAPS is emphasis, not an acronym
        proper.add(low)
    uni, seen = [], set()
    for tok in toks:
        if tok in proper and tok not in seen:
            seen.add(tok)
            uni.append(tok)
            if len(uni) >= max_unigrams:
                break
    return bigrams + uni


def shared_pairs(topic_urls: dict[str, list[str]]) -> dict[tuple, list[str]]:
    """URL sets per topic -> {(topic_a, topic_b): [shared urls]}."""
    by_url: dict[str, set[str]] = {}
    for topic, urls in topic_urls.items():
        for u in urls:
            by_url.setdefault(u, set()).add(topic)
    pairs: dict[tuple, list[str]] = {}
    for url, topics in by_url.items():
        ts = sorted(topics)
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                pairs.setdefault((a, b), []).append(url)
    return pairs
