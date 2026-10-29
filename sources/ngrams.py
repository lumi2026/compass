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
import string
import time
import urllib.request

SNAP_BASE = "https://data.gdeltproject.org/gdeltv3/webngrams"
UA = {"User-Agent": "compass-hackathon/0.1"}
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


def _words(s: str) -> list[str]:
    return [w.translate(STRIP).lower() for w in s.split() if w.translate(STRIP)]


def match_record(r: dict, phrase: list[str]) -> bool:
    """Multi-gram match per the NGrams 3.0 doc: the middle word of the
    phrase is the ngram, words before it are the tail of pre, words
    after it are the head of post. Unigram = exact ngram match."""
    if r.get("type") != 1 or not phrase:
        return False
    mid = (len(phrase) - 1) // 2  # bigram: ngram is the FIRST word (doc rule)
    if r.get("ngram", "").translate(STRIP).lower() != phrase[mid]:
        return False
    pre, post = _words(r.get("pre", "")), _words(r.get("post", ""))
    before, after = phrase[:mid], phrase[mid + 1:]
    return pre[len(pre) - len(before):] == before and post[: len(after)] == after


def ngram_mentions(
    terms: list[str],
    snapshot_gz: str,
    lang: str = "en",
    per_term_cap: int = 5,
) -> dict:
    """Count + sample uni/multi-gram hits per term in a snapshot file.

    Streams the gzip (never loads all rows). Phrases use pre/post rules.
    Returns {term: {"count": int, "urls": [...], "samples": [records]}}.
    """
    want = {t.lower(): t.lower().split() for t in terms}
    counts: dict[str, int] = {t: 0 for t in want}
    urls: dict[str, set] = {t: set() for t in want}
    samples: dict[str, list] = {t: [] for t in want}
    with gzip.open(snapshot_gz, "rt") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("lang") != lang:
                continue
            for t, phrase in want.items():
                if match_record(r, phrase):
                    counts[t] += 1
                    urls[t].add(r.get("url", ""))
                    if len(samples[t]) < per_term_cap:
                        samples[t].append(r)
    return {
        t: {"count": counts[t], "urls": sorted(urls[t]), "samples": samples[t]}
        for t in want
    }
