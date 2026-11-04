"""LangSmith tracing setup. Uses the SDK's own env names. Opt-in.

Tracing turns on only when the operator sets LANGSMITH_TRACING=true
*and* LANGSMITH_API_KEY (see .env) is present. A key alone never
enables it. Defaults LANGSMITH_PROJECT to "compass-dev". .env fills gaps
only; real env always wins. No key is printed or logged.
"""

import os

DEFAULT_PROJECT = "compass-dev"
DEFAULT_ENDPOINT = "https://api.smith.langchain.com"


def setup_langsmith(project: str | None = None,
                    endpoint: str | None = None,
                    dotenv_path: str = ".env") -> dict:
    """Enable LangChain -> LangSmith tracing from repo env. Idempotent.

    Returns {"enabled": bool, "project": str, "endpoint": str}.
    """
    try:
        from services.kalshi_auth import load_dotenv
        load_dotenv(dotenv_path)
    except Exception:
        pass

    key = os.environ.get("LANGSMITH_API_KEY", "")

    ep = endpoint or os.environ.get("LANGSMITH_ENDPOINT") or DEFAULT_ENDPOINT
    os.environ.setdefault("LANGSMITH_ENDPOINT", ep)

    proj = project or os.environ.get("LANGSMITH_PROJECT") or DEFAULT_PROJECT
    os.environ.setdefault("LANGSMITH_PROJECT", proj)

    enabled = False
    if key:
        tracing = os.environ.get("LANGSMITH_TRACING", "")
        if tracing.lower() in ("true", "1", "yes"):
            os.environ["LANGSMITH_TRACING"] = "true"
            enabled = True
    return {"enabled": enabled, "project": proj, "endpoint": ep}


def is_enabled() -> bool:
    """True when tracing was opted in and a key is present."""
    return (os.environ.get("LANGSMITH_TRACING") == "true"
            and bool(os.environ.get("LANGSMITH_API_KEY", "")))


from contextlib import contextmanager  # noqa: E402


@contextmanager
def squad_span(name: str, inputs: dict | None = None):
    """Root span for one squad run. No-op when tracing is off.

    Yields an object with an `outputs` attribute the caller fills.
    Flushes explicitly so the run lands even when the process exits
    right after (background batches would otherwise be lost). Tracing
    infra failures warn and continue untraced; squad errors propagate.
    """
    from types import SimpleNamespace

    if not is_enabled():
        yield SimpleNamespace(outputs=None)
        return
    import uuid

    from langsmith import Client

    client = Client()
    rid = uuid.uuid4()
    state = SimpleNamespace(outputs=None)
    try:
        client.create_run(name=name, inputs=inputs or {}, run_type="chain",
                          project_name=os.environ.get("LANGSMITH_PROJECT",
                                                      DEFAULT_PROJECT),
                          id=rid)
    except Exception as exc:
        print(f"[tracing] create_run failed ({exc}); continuing untraced")
        yield state
        return
    try:
        yield state
    except Exception as exc:
        _finish_span(client, rid, error=f"{type(exc).__name__}: {exc}")
        raise
    else:
        _finish_span(client, rid, outputs=state.outputs or {})


def _finish_span(client, rid, outputs=None, error=None):
    """End the span and flush. Never raises: tracing must not sink runs."""
    from datetime import datetime, timezone

    try:
        if error is not None:
            client.update_run(run_id=rid, error=error,
                              end_time=datetime.now(timezone.utc))
        else:
            client.update_run(run_id=rid, outputs=outputs or {},
                              end_time=datetime.now(timezone.utc))
        client.flush()
    except Exception as exc:
        print(f"[tracing] finish failed ({exc})")
