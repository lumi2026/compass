"""Step 3 gates: discovery, auto topics, graph publish. No network."""

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.feed import FeedStore
from services.listener import KalshiListener, TickerState, Update
from services.server import Service, event_of, price_cents
from sources.base import MarketEvent
from sources.kalshi import MarketContract


def _market(ticker, event, vol, price=50):
    return MarketContract(source="kalshi", ticker=ticker, event_ticker=event,
                          title=f"Title {ticker}", yes_bid=price,
                          yes_ask=price + 2, last_price=price,
                          volume_24h=vol)


class _StubSrc:
    def __init__(self, events, markets, raws=None):
        self._events = events
        self._markets = markets
        self._raws = raws or {}
        self.fail_events = set()

    def fetch_normalized(self, limit=5):
        return [MarketEvent(source="kalshi", external_id=e, title=f"Event {e}",
                            raw=self._raws.get(e, {}))
                for e in self._events[:limit]]

    def fetch_markets(self, event_ticker, limit=20):
        if event_ticker in self.fail_events:
            raise RuntimeError("delisted")
        return self._markets.get(event_ticker, [])


def _service(monkey=None):
    svc = Service(KalshiListener(None), FeedStore())
    stub = _StubSrc(
        ["E1", "E2"],
        {"E1": [_market("E1-A", "E1", 900), _market("E1-B", "E1", 100)],
         "E2": [_market("E2-A", "E2", 10)]})
    svc.src = stub
    return svc, stub


def test_event_of_and_price_cents():
    assert event_of("KXWARSHOUT-26JUL-WARSH") == "KXWARSHOUT-26JUL"
    assert event_of("T1") == "T1"
    assert price_cents({"last_price_dollars": "0.5500"}) == 55
    assert price_cents({"last_price": 57}) == 57
    assert price_cents({"yes_bid": 60}) == 60
    assert price_cents({}) is None
    assert price_cents({"last_price": "junk"}) is None


def test_discover_events():
    svc, _ = _service()
    assert svc.discover_events(5) == [
        {"event_ticker": "E1", "title": "Event E1"},
        {"event_ticker": "E2", "title": "Event E2"}]


def test_auto_topics_ranks_by_volume():
    svc, _ = _service()
    assert svc.auto_topics(top_k=1) == ["E1-A", "E1-B"]
    assert svc.ticker_event["E1-A"] == "E1"
    assert svc.event_titles["E1"] == "Event E1"
    # a failing event never blocks the rotation
    svc2, stub = _service()
    stub.fail_events.add("E1")
    assert svc2.auto_topics(top_k=5) == ["E2-A"]


def test_auto_topics_records_event_meta():
    svc, stub = _service()
    stub._raws = {"E1": {"category": "Elections", "series_ticker": "KXE1",
                         "sub_title": "Before 2040", "junk": [1]}}
    assert svc.auto_topics(top_k=1) == ["E1-A", "E1-B"]
    assert svc.event_meta["E1"] == {"category": "Elections",
                                   "series_ticker": "KXE1",
                                   "sub_title": "Before 2040"}
    assert "E2" not in svc.event_meta


def test_proposals_for_event_carries_meta_props():
    svc, _ = _service()
    svc.event_titles["E1"] = "Event E1"
    svc.event_meta["E1"] = {"category": "Elections"}
    svc.ticker_event["E1-A"] = "E1"
    props = svc._proposals_for(
        "E1-A", {"title": "Title E1-A", "last_price": 55}, 0)
    assert props[0]["entity_id"] == "kalshi:E1"
    assert json.loads(props[0]["detail"]) == {
        "kind": "Event", "title": "Event E1",
        "props": {"category": "Elections"}}


def test_proposals_for_market_carries_event_ticker():
    svc, _ = _service()
    svc.ticker_event["E1-A"] = "E1"
    props = svc._proposals_for(
        "E1-A", {"title": "Title E1-A", "last_price": 55}, 0)
    market = json.loads(props[1]["detail"])
    assert market["props"]["event_ticker"] == "E1"
    assert market["props"]["price_cents"] == 55


def test_proposals_for_event_without_meta_has_no_props():
    svc, _ = _service()
    svc.ticker_event["E1-A"] = "E1"
    props = svc._proposals_for(
        "E1-A", {"title": "Title E1-A", "last_price": 55}, 0)
    assert json.loads(props[0]["detail"]) == {
        "kind": "Event", "title": "E1"}


def test_set_topics_records_mapping():
    svc, _ = _service()
    assert svc.set_topics(["E2"], ["X-9"]) == ["E2-A", "X-9"]
    assert svc.ticker_event["E2-A"] == "E2"
    assert svc.ticker_event["X-9"] == "X-9"  # numeric suffix: no event to strip


class _Capture(BaseHTTPRequestHandler):
    def _ok(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.server.seen.append(json.loads(self.rfile.read(length) or b"{}"))
        self._ok({"revision": len(self.server.seen), "verdicts": [],
                  "derived_fresh": 0})

    def log_message(self, *args):
        pass


def _graph_stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    srv.seen = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


def _upd(ticker, ts_ms):
    return Update(kind="ticker", ticker=ticker, payload={}, ts_ms=ts_ms)


def test_publish_baseline_then_move_then_small():
    srv, t = _graph_stub()
    try:
        svc, _ = _service()
        svc.set_graph_publish(
            f"http://127.0.0.1:{srv.server_address[1]}", 5)
        svc.ticker_event["T1"] = "E1"
        svc.store.ingest_single(
            "T1", TickerState(ticker="T1",
                             last={"title": "Seed?", "last_price": 55},
                             updated_ms=1))
        r1 = svc.maybe_publish(_upd("T1", 1793394000000))
        assert r1["published"] and r1["proposals"] == 2  # baseline, no move
        kinds = [p["action"] for p in srv.seen[-1]["proposals"]]
        assert kinds == ["add-node", "add-node"]
        market_detail = json.loads(srv.seen[-1]["proposals"][1]["detail"])
        assert market_detail["props"]["price_cents"] == 55
        assert market_detail["props"]["odds_at"]

        svc.store.ingest_single(
            "T1", TickerState(ticker="T1",
                             last={"title": "Seed?", "last_price": 63},
                             updated_ms=2))
        r2 = svc.maybe_publish(_upd("T1", 1793394060000))
        assert r2["published"] and r2["proposals"] == 4
        last_two = srv.seen[-1]["proposals"][-2:]
        assert last_two[0]["entity_id"].startswith("happening:market-move-T1-")
        assert "moved 55c -> 63c" in last_two[0]["detail"]
        assert json.loads(last_two[0]["detail"])["props"] == {
            "market": "T1", "old_cents": 55, "new_cents": 63,
            "move_cents": 8}
        assert last_two[1]["detail"] and "relates-to" in last_two[1]["detail"]

        svc.store.ingest_single(
            "T1", TickerState(ticker="T1",
                             last={"title": "Seed?", "last_price": 64},
                             updated_ms=3))
        r3 = svc.maybe_publish(_upd("T1", 1793394120000))
        assert r3["published"] and r3["proposals"] == 2  # +1c < threshold
    finally:
        srv.shutdown()
        t.join(timeout=10)


def test_sweep_pushes_live_odds_without_socket():
    srv, t = _graph_stub()
    try:
        svc, _ = _service()
        svc.set_topics(["E1"], [])
        svc.set_graph_publish(
            f"http://127.0.0.1:{srv.server_address[1]}", 5)
        r = svc.sweep()
        assert r["published"] and r["tickers"] == 2 and r["proposals"] == 4
        markets = [p for p in srv.seen[-1]["proposals"]
                   if p["entity_id"].startswith("market:")]
        assert len(markets) == 2
        assert json.loads(markets[0]["detail"])["props"]["price_cents"] == 50
        # second sweep: same prices -> idempotent duplicates, no happenings
        r2 = svc.sweep()
        assert r2["published"] and r2["proposals"] == 4
        kinds = {p["action"] for p in srv.seen[-1]["proposals"]}
        assert kinds == {"add-node"}
    finally:
        srv.shutdown()
        t.join(timeout=10)


def test_sweep_needs_graph_and_tickers():
    svc, _ = _service()
    assert svc.sweep()["reason"] == "no graph_url"
    svc.set_graph_publish("http://127.0.0.1:1/")
    assert svc.sweep()["reason"] == "no tickers"


def test_publish_disabled_and_unreachable():
    svc, _ = _service()
    assert svc.maybe_publish(_upd("T1", 1))["reason"] == "no graph_url"
    svc.set_graph_publish("http://127.0.0.1:1/", 5)  # nothing listens
    r = svc.maybe_publish(_upd("T1", 1))
    assert r["published"] is False and "unreachable" in r["reason"]
    assert svc.set_graph_publish("") == {"graph_url": "",
                                         "move_threshold_cents": 5}


def _http(method, url, payload=None, timeout=10):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_handler_routes_for_step3():
    import urllib.error  # noqa: F401 (keeps handler error path importable)

    from services.server import serve

    svc, _ = _service()
    httpd = serve(svc, 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        code, body = _http("GET", base + "/events?limit=1")
        assert code == 200 and body == {
            "events": [{"event_ticker": "E1", "title": "Event E1"}]}
        code, body = _http("GET", base + "/events?limit=nope")
        assert code == 400
        code, body = _http("POST", base + "/topics",
                           {"auto": True, "top_k": 1})
        assert code == 200 and body["tickers"] == ["E1-A", "E1-B"]
        assert body["mode"] == "auto"
        code, body = _http("POST", base + "/topics", {"events": ["E2"]})
        assert code == 200 and body["mode"] == "manual"
        assert body["tickers"] == ["E2-A"]
        code, body = _http("POST", base + "/publish/graph",
                           {"graph_url": "http://g:8090/x/",
                            "move_threshold_cents": 7})
        assert code == 200 and body == {"graph_url": "http://g:8090/x",
                                        "move_threshold_cents": 7}
        code, body = _http("GET", base + "/markets?event=E1")
        assert code == 200
        assert [m["ticker"] for m in body["markets"]] == ["E1-A", "E1-B"]
        assert body["markets"][0]["volume_24h"] == 900
        code, _ = _http("GET", base + "/markets")
        assert code == 400
        code, body = _http("GET", base + "/publish/graph")
        assert code == 200 and body["graph_url"] == "http://g:8090/x"
        code, body = _http("POST", base + "/publish/graph",
                           {"graph_url": "ftp://nope"})
        assert code == 400
        code, _ = _http("POST", base + "/publish/graph",
                        {"graph_url": "http://g:8090",
                         "move_threshold_cents": "many"})
        assert code == 400
        code, body = _http("GET", base + "/health")
        assert code == 200 and body["graph_url"] == "http://g:8090/x"
        assert body["move_threshold_cents"] == 7
    finally:
        httpd.shutdown()
        t.join(timeout=10)
