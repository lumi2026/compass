"""Layer 1 contracts: information source plugins.

Each source implements SourcePlugin. The orchestrator only talks to
this interface, so adding a feed means adding a plugin, not rewiring.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class MarketEvent:
    source: str
    external_id: str
    title: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsArticle:
    source: str
    external_id: str
    title: str
    url: str
    published_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class SourcePlugin(Protocol):
    """Contract every information source must satisfy."""

    name: str

    def fetch_raw(self, limit: int = 5) -> list[dict[str, Any]]:
        """Fetch raw payloads from the upstream API."""
        ...

    def normalize(self, raw: dict[str, Any]) -> MarketEvent | NewsArticle:
        """Normalize one raw payload to a typed item."""
        ...

    def fetch_normalized(self, limit: int = 5) -> list[MarketEvent | NewsArticle]:
        """Convenience: fetch then normalize. Default loop, override if needed."""
        ...
