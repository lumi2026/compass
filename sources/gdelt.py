"""GDELT DOC 2.0 source plugin. Public artlist queries, JSON output."""

import hashlib
import json
import urllib.parse
import urllib.request

from .base import NewsArticle, SourcePlugin

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT_S = 20


def _get_json(url: str, retries: int = 3) -> dict:
    import time
    import urllib.error

    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "compass/0.1"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429 or attempt == retries - 1:
                raise
            # GDELT asks for max 1 req / 5s; back off well above that.
            time.sleep(7 * (attempt + 1))
    raise last  # pragma: no cover


class GdeltSource:
    """SourcePlugin for GDELT news (DOC API, mode=artlist)."""

    name = "gdelt"

    def __init__(
        self, query: str = "climate", timespan: str = "1week", sort: str | None = None
    ) -> None:
        self.query = query
        self.timespan = timespan
        self.sort = sort

    def fetch_raw(self, limit: int = 5) -> list[dict]:
        q = {
            "query": self.query,
            "mode": "artlist",
            "maxrecords": limit,
            "timespan": self.timespan,
            "format": "json",
        }
        if self.sort:
            q["sort"] = self.sort
        params = urllib.parse.urlencode(q)
        data = _get_json(f"{API_URL}?{params}")
        articles = data.get("articles", data if isinstance(data, list) else [])
        return articles[:limit]

    def normalize(self, raw: dict) -> NewsArticle:
        url = str(raw.get("url", ""))
        title = str(raw.get("title", url))
        external_id = hashlib.sha256(url.encode()).hexdigest()[:16] if url else title[:16]
        return NewsArticle(
            source=self.name,
            external_id=external_id,
            title=title,
            url=url,
            published_at=str(raw.get("seendate", "")),
            raw=raw,
        )

    def fetch_normalized(self, limit: int = 5) -> list[NewsArticle]:
        return [self.normalize(r) for r in self.fetch_raw(limit)]
