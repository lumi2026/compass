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
        from services.auth import load_dotenv
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
