"""Kalshi HTTP layer: transient 400s retried once, errors carry bodies."""

import io
import json
import urllib.error

import pytest

from sources import kalshi as K


class _Resp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return self._buf.read(n)


def _http_error(code, body: bytes):
    return urllib.error.HTTPError("http://x/events", code, "Bad Request",
                                  {}, io.BytesIO(body))


def _script(monkeypatch, outcomes):
    calls = {"n": 0, "sleeps": []}
    monkeypatch.setattr("time.sleep", lambda s: calls["sleeps"].append(s))

    def fake(req, timeout=None):
        calls["n"] += 1
        out = outcomes[min(calls["n"] - 1, len(outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return _Resp(out)

    monkeypatch.setattr(K.urllib.request, "urlopen", fake)
    return calls


def test_transient_400_retried_once_then_succeeds(monkeypatch):
    calls = _script(monkeypatch,
                    [_http_error(400, b'{"error":"flaky"}'),
                     {"events": []}])
    assert K._get_json("http://x/events") == {"events": []}
    assert calls["n"] == 2
    assert calls["sleeps"] == [2]


def test_persistent_400_raises_with_body(monkeypatch):
    calls = _script(monkeypatch,
                    [_http_error(400, b'{"error":"bad limit"}')])
    with pytest.raises(urllib.error.HTTPError) as ei:
        K._get_json("http://x/events")
    assert ei.value.code == 400
    assert "bad limit" in str(ei.value)
    assert calls["n"] == 2  # one retry, then give up loudly
    assert calls["sleeps"] == [2]


def test_429_backoff_unchanged(monkeypatch):
    calls = _script(monkeypatch,
                    [_http_error(429, b"slow down"), {"events": [1]}])
    assert K._get_json("http://x/events") == {"events": [1]}
    assert calls["n"] == 2
    assert calls["sleeps"] == [2]


def test_non_http_errors_propagate_untouched(monkeypatch):
    boom = urllib.error.URLError("dns down")
    _script(monkeypatch, [boom])
    with pytest.raises(urllib.error.URLError):
        K._get_json("http://x/events")
