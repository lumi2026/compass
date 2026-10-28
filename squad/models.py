"""LangChain model factory. Single place that decides which chat model
the squad uses. Returns None when no key is configured so agents fall
back to their offline implementations (and tests stay network-free).

Backend: Muse Spark via Meta's OpenAI-compatible endpoint.

Env:
  MODEL_API_KEY — required.
  COMPASS_MODEL — model id, default "muse-spark-1.3".
"""

import os

BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


def make_chat_model():
    """Return a LangChain chat model, or None when unconfigured."""
    if not os.environ.get("MODEL_API_KEY"):
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=BASE_URL,
        api_key=os.environ["MODEL_API_KEY"],
        model=os.environ.get("COMPASS_MODEL", DEFAULT_MODEL),
    )
