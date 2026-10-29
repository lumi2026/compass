"""LangSmith tracing setup tests. No network: env mapping only."""

import os

from squad import tracing as tr


def _clean(monkeypatch):
    for k in ("LANGSMITH_API_KEY", "LANGSMITH_TRACING",
              "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)


def test_opt_in_enables_tracing(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "k-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    out = tr.setup_langsmith(dotenv_path=str(tmp_path / "missing.env"))
    assert out["enabled"] is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert out["project"] == "compass-dev"


def test_key_alone_stays_disabled(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "k-test")
    out = tr.setup_langsmith(dotenv_path=str(tmp_path / "missing.env"))
    assert out["enabled"] is False
    assert os.environ.get("LANGSMITH_TRACING", "") != "true"


def test_flag_without_key_stays_disabled(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    out = tr.setup_langsmith(dotenv_path=str(tmp_path / "missing.env"))
    assert out["enabled"] is False


def test_explicit_opt_out_respected(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "k-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    out = tr.setup_langsmith(dotenv_path=str(tmp_path / "missing.env"))
    assert out["enabled"] is False


def test_custom_project_respected(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "k-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    out = tr.setup_langsmith(project="demo-1",
                             dotenv_path=str(tmp_path / "missing.env"))
    assert out["project"] == "demo-1"
    assert os.environ["LANGSMITH_PROJECT"] == "demo-1"
