"""Cross-section seed capture: bucketing without network."""

from sources.kalshi import Candle, MarketContract, OrderBookSnapshot


class _StubSource:
    def __init__(self, *a, **k):
        pass

    def fetch_raw(self, limit=5):
        return [
            {"event_ticker": "E-POL", "category": "Elections"},
            {"event_ticker": "E-POL2", "category": "Politics"},
            {"event_ticker": "E-POL3", "category": "Politics"},
            {"event_ticker": "E-ECO", "category": "Economics"},
            {"event_ticker": "E-SPO", "category": "Sports"},
            {"event_ticker": "E-???", "category": "Companies"},
        ][:limit]

    def fetch_markets(self, event_ticker, limit=20):
        return [MarketContract(source="kalshi", ticker=f"{event_ticker}-M",
                               event_ticker=event_ticker, title="M",
                               last_price=55)]

    def fetch_orderbook(self, ticker, depth=10):
        return OrderBookSnapshot(ticker=ticker, yes_bids=[], no_bids=[])

    def fetch_candlesticks(self, tickers, start_ts, end_ts,
                           period_interval=60):
        return {t: [Candle(ticker=t, end_ts=1, open=50, high=60,
                           low=48, close=55, volume=3)] for t in tickers}


def test_parse_cross_section():
    from services.seed import parse_cross_section

    assert parse_cross_section("politics:Politics,Elections sports:Sports") == {
        "politics": ("Politics", "Elections"), "sports": ("Sports",)}
    assert parse_cross_section("") == {}


def test_capture_cross_section_buckets(monkeypatch):
    import services.seed as seed

    monkeypatch.setattr(seed, "KalshiSource", _StubSource)
    payload = seed.capture_cross_section(
        {"politics": ("Politics", "Elections"),
         "economics": ("Economics",),
         "sports": ("Sports",)},
        per_category=2, markets_per_event=1)

    assert payload["categories"] == {
        "E-POL": "politics", "E-POL2": "politics",
        "E-ECO": "economics", "E-SPO": "sports"}
    assert sorted(payload["tickers"]) == [
        "E-ECO-M", "E-POL-M", "E-POL2-M", "E-SPO-M"]
    # Third politics event stays out: bucket is full.
    assert "E-POL3-M" not in payload["tickers"]
    # Unmapped native category never captured.
    assert "E-???-M" not in payload["tickers"]
    entry = payload["tickers"]["E-POL-M"]
    assert entry["last"]["last_price"] == 55
    assert entry["candles"][0]["close"] == 55
