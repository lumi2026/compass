"""Contract tests for the Kalshi live service. No network: fixtures only."""

import base64
import json

from services.feed_store import FeedStore
from services.kalshi_auth import (
    auth_headers,
    load_credentials,
    presign_text,
    sign_request,
)
from services.kalshi_listener import (
    TickerState,
    Update,
    apply_delta,
    handle_message,
    subscribe_payload,
)
from services.kalshi_server import Service
from sources.kalshi import OrderBookSnapshot


def _ed_key():
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return priv, pem


def _rsa_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return priv, pem


def test_presign_text_strips_query():
    assert presign_text(123, "get", "/trade-api/ws/v2?x=1") == b"123GET/trade-api/ws/v2"


def test_sign_ed25519_roundtrip():
    priv, pem = _ed_key()
    sig = sign_request(123, "GET", "/trade-api/ws/v2", pem)
    priv.public_key().verify(base64.b64decode(sig), b"123GET/trade-api/ws/v2")


def test_sign_rsa_roundtrip():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    priv, pem = _rsa_key()
    sig = sign_request(456, "GET", "/trade-api/v2/events?limit=1", pem)
    priv.public_key().verify(
        base64.b64decode(sig),
        b"456GET/trade-api/v2/events",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256.digest_size),
        hashes.SHA256(),
    )


def test_dotenv_fills_gaps_only(tmp_path, monkeypatch):
    from services.kalshi_auth import load_credentials

    dotenv = tmp_path / ".env"
    dotenv.write_text('KALSHI_KEY_ID=fromfile\nKALSHI_PRIVATE_KEY="k=1"\n# comment\nBLANK=\n')
    monkeypatch.delenv("KALSHI_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "")
    creds = load_credentials(dotenv_path=str(dotenv))
    assert creds is not None and creds.key_id == "fromfile"
    assert creds.private_key_pem == "k=1"
    monkeypatch.setenv("KALSHI_KEY_ID", "realenv")
    assert load_credentials(dotenv_path=str(dotenv)).key_id == "realenv"


def test_auth_headers_shape_and_no_creds_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("KALSHI_KEY_ID", raising=False)
    assert load_credentials(dotenv_path=str(tmp_path / "none")) is None
    headers = auth_headers("GET", "/trade-api/ws/v2", type("C", (), {"key_id": "k", "private_key_pem": _ed_key()[1]})())
    assert set(headers) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}


def test_subscribe_payload_format():
    payload = json.loads(subscribe_payload(2, ["ticker", "orderbook_delta"], ["T1"]))
    assert payload == {"id": 2, "cmd": "subscribe",
                       "params": {"channels": ["ticker", "orderbook_delta"], "market_tickers": ["T1"]}}


def test_handle_ticker_message():
    state: dict[str, TickerState] = {}
    updates = handle_message({"type": "ticker", "sending_ts_ms": 7,
                              "msg": {"market_ticker": "T1", "last_price": 55}}, state)
    assert len(updates) == 1 and updates[0].kind == "ticker"
    assert state["T1"].last["last_price"] == 55


def test_handle_snapshot_and_delta():
    state: dict[str, TickerState] = {}
    handle_message({"type": "orderbook_snapshot", "msg": {
        "market_ticker": "T1",
        "yes_dollars_fp": [["0.5500", "10.00"]],
        "no_dollars_fp": [["0.4400", "7.00"]]}}, state)
    book = state["T1"].orderbook
    assert book is not None and book.yes_bids[0].price_cents == 55
    handle_message({"type": "orderbook_delta", "msg": {
        "market_ticker": "T1", "side": "yes",
        "price_dollars": "0.5600", "delta_fp": "3.00"}}, state)
    assert [lv.price_cents for lv in state["T1"].orderbook.yes_bids] == [56, 55]
    handle_message({"type": "orderbook_delta", "msg": {
        "market_ticker": "T1", "side": "yes",
        "price_dollars": "0.5500", "delta_fp": "0.00"}}, state)
    assert [lv.price_cents for lv in state["T1"].orderbook.yes_bids] == [56]


def test_handle_ignores_junk():
    state: dict[str, TickerState] = {}
    assert handle_message({"type": "ticker", "msg": {}}, state) == []
    assert handle_message({"type": "bogus", "msg": {"market_ticker": "T"}}, state) == []
    assert handle_message({"type": "trade", "msg": {"market_ticker": "T2", "price": 1}}, state)[0].kind == "trade"


def test_apply_delta_sorted_and_removed():
    from sources.kalshi import PriceLevel

    book = OrderBookSnapshot(ticker="T", yes_bids=[PriceLevel(55, 1)], no_bids=[])
    out = apply_delta(book, "yes", 54, 2)
    assert [lv.price_cents for lv in out.yes_bids] == [55, 54]
    out = apply_delta(out, "yes", 55, 0)
    assert [lv.price_cents for lv in out.yes_bids] == [54]


def test_feed_store_snapshot_recent_and_webhooks():
    store = FeedStore()
    assert store.subscribe_webhook("not-a-url") is False
    assert store.subscribe_webhook("http://agent:9/hook") is True
    store.ingest_state({"T1": TickerState(ticker="T1", last={"last_price": 60})})
    snap = store.snapshot(["T1", "MISSING"])
    assert list(snap) == ["T1"] and snap["T1"]["last"]["last_price"] == 60
    store.publish(Update(kind="ticker", ticker="T1", payload={"p": 1}, ts_ms=9))
    assert store.recent("T1")[0]["ts_ms"] == 9
    # unreachable webhook is auto-discarded on failed push (dead-target contract)
    assert store.webhooks == set()
    assert store.unsubscribe_webhook("http://agent:9/hook") is False


def test_merge_state_never_blanks_seed():
    from services.feed_store import FeedStore
    from services.kalshi_listener import TickerState

    store = FeedStore()
    store.ingest_single("T1", TickerState(ticker="T1", last={"title": "Seed?"}))
    store.merge_state({"T1": TickerState(ticker="T1", last={}, updated_ms=5)})
    assert store.snapshot(["T1"])["T1"]["last"] == {"title": "Seed?"}


def test_service_set_topics_with_direct_tickers():
    from services.kalshi_listener import KalshiListener

    service = Service(KalshiListener(None), FeedStore())
    assert service.set_topics([], ["B", "A", "B"]) == ["A", "B"]


def test_wire_graph_publish_disabled_without_url():
    from scripts.kalshi_service import wire_graph_publish
    from services.kalshi_listener import KalshiListener

    service = Service(KalshiListener(None), FeedStore())
    assert wire_graph_publish(service, "") is None
    assert service.graph_url == ""
    # A sweep without wiring reports the same reason as the HTTP path.
    assert wire_graph_publish(service, "", sweep=True) == {
        "published": False, "reason": "no graph_url"}
    assert service.graph_url == ""


def test_wire_graph_publish_sets_url_and_sweeps_once():
    from scripts.kalshi_service import wire_graph_publish
    from services.kalshi_listener import KalshiListener

    service = Service(KalshiListener(None), FeedStore())
    sweeps: list[bool] = []
    service.sweep = lambda: sweeps.append(True) or {
        "published": True, "tickers": 2}
    assert wire_graph_publish(
        service, "http://127.0.0.1:8090/", 7, sweep=True) == {
            "published": True, "tickers": 2}
    assert service.graph_url == "http://127.0.0.1:8090"
    assert service.move_threshold_cents == 7
    assert sweeps == [True]
    # Wiring without --sweep must not publish anything.
    assert wire_graph_publish(service, "http://127.0.0.1:8090") is None
    assert sweeps == [True]


def test_cli_rejects_non_http_graph_url():
    from click.testing import CliRunner

    from scripts.kalshi_service import main

    result = CliRunner().invoke(main, ["--graph-url", "ftp://nope"])
    assert result.exit_code == 2
    assert "http://" in result.output


def test_seed_load_roundtrip(tmp_path):
    from services.feed_seed import load, save
    from services.feed_store import FeedStore

    payload = {"tickers": {"T1": {
        "last": {"title": "Seed?", "last_price": 55},
        "yes": [[55, 10]], "no": [],
        "candles": [{"end_ts": 1, "open": 50, "high": 60, "low": 48,
                     "close": 55, "volume": 3}]}}}
    path = str(tmp_path / "seed.json")
    save(path, payload)
    store = FeedStore()
    assert load(path, store) == ["T1"]
    snap = store.snapshot(["T1"])
    assert snap["T1"]["top_yes"] == {"price_cents": 55, "contracts": 10}
    assert store.recent("T1")


def test_listener_session_against_local_socket():
    """End-to-end socket path: subscribe wire format in, ticker update out."""
    import asyncio

    import websockets

    from services.kalshi_auth import Credentials
    from services.kalshi_listener import KalshiListener

    seen_subscribe: list[dict] = []
    received: list = []

    async def handler(ws):
        seen_subscribe.append(json.loads(await ws.recv()))
        await ws.send(json.dumps(
            {"type": "ticker", "sending_ts_ms": 11,
             "msg": {"market_ticker": "T9", "last_price": 61}}))
        await ws.close()

    async def scenario():
        server = await websockets.serve(handler, "127.0.0.1", 18765)
        _, pem = _ed_key()
        listener = KalshiListener(
            Credentials(key_id="k", private_key_pem=pem),
            on_update=received.append,
            url="ws://127.0.0.1:18765/trade-api/ws/v2",
        )
        listener.watch(["T9"])
        await listener._session()
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())
    assert seen_subscribe and seen_subscribe[0]["cmd"] == "subscribe"
    assert seen_subscribe[0]["params"]["market_tickers"] == ["T9"]
    assert len(received) == 1 and received[0].ticker == "T9"
    assert received[0].payload["last_price"] == 61
