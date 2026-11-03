"""LangChain model factory. Single place that decides which chat model
the squad uses. Returns None when no key is configured so agents fall
back to their offline implementations (and tests stay network-free).

All bundled providers speak the OpenAI chat API, so one
OpenAI-compatible client covers them — only base_url / key / model
id change. The agent tools (`llm_relevant`, `llm_summarize`) only
call `model.invoke` with plain System/Human messages, so any
LangChain chat model also works when passed directly as
`NewsAgent(model=...)`.

Env:
  MODEL_PROVIDER — meta (default) | openai | deepseek | custom.
    COMPASS_PROVIDER is accepted as an alias.
  MODEL_API_KEY — generic key (all providers). Provider-specific keys
    also work: OPENAI_API_KEY, DEEPSEEK_API_KEY.
  COMPASS_MODEL (or MODEL_NAME) — model id, overrides the preset default.
  MODEL_BASE_URL — overrides the preset base_url (or sets it for custom).

Tracing (see squad.tracing.setup_langsmith, called by scripts/run_*):
  opt-in via LANGSMITH_TRACING=true plus LANGSMITH_API_KEY.
  LANGSMITH_PROJECT (default "compass-dev"), LANGSMITH_ENDPOINT.

Examples:
  meta:     MODEL_PROVIDER=meta MODEL_API_KEY=... (default muse-spark-1.3-contributor)
  gpt:      MODEL_PROVIDER=openai OPENAI_API_KEY=... [COMPASS_MODEL=gpt-4o-mini]
  deepseek: MODEL_PROVIDER=deepseek DEEPSEEK_API_KEY=... [COMPASS_MODEL=deepseek-chat]
  custom:   MODEL_PROVIDER=custom MODEL_BASE_URL=https://.../v1 MODEL_API_KEY=...
"""

import os
import re

BASE_URL = "https://api.meta.ai/v1"  # kept: the meta preset (backward compat)
DEFAULT_MODEL = "muse-spark-1.3-contributor"  # demo default: reasoning model

# Role -> default model id. Collectors get the fast non-reasoning model;
# reasoning roles (graph building, investigation) get the contributor
# variant. Operator env always wins: MODEL_FOR_<ROLE> first, then the
# global COMPASS_MODEL / MODEL_NAME, then this table.
AGENT_MODEL_DEFAULTS = {
    "news-agent": "muse-spark-1.3-contributor",
    "graph-agent": "muse-spark-1.3-contributor",
    "scout": "muse-spark-1.3",
    "investigator": "muse-spark-1.3-contributor",
    "connector": "muse-spark-1.3-contributor",
    "brain": "muse-spark-1.3-contributor",
}


def _role_env(role: str) -> str:
    return "MODEL_FOR_" + re.sub(r"[^A-Z0-9]", "_", role.upper())

PRESETS = {
    "meta": {
        "base_url": BASE_URL,
        "default_model": DEFAULT_MODEL,
        "key_envs": ("MODEL_API_KEY",),
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "key_envs": ("OPENAI_API_KEY", "MODEL_API_KEY"),
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "key_envs": ("DEEPSEEK_API_KEY", "MODEL_API_KEY"),
    },
}


def make_chat_model(provider: str | None = None,
                    model: str | None = None):
    """Return a LangChain chat model, or None when unconfigured.

    `provider` overrides MODEL_PROVIDER/COMPASS_PROVIDER; `model`
    overrides COMPASS_MODEL/MODEL_NAME. "custom" provider requires
    MODEL_BASE_URL. Unknown names raise ValueError.
    """
    prov = (provider or os.environ.get("MODEL_PROVIDER")
            or os.environ.get("COMPASS_PROVIDER") or "meta").lower()
    if prov == "custom":
        base_url = os.environ.get("MODEL_BASE_URL", "")
        if not base_url:
            raise ValueError("MODEL_PROVIDER=custom requires MODEL_BASE_URL")
        key_envs: tuple[str, ...] = ("MODEL_API_KEY",)
        default_model = DEFAULT_MODEL
    elif prov in PRESETS:
        preset = PRESETS[prov]
        base_url = os.environ.get("MODEL_BASE_URL") or preset["base_url"]
        key_envs = preset["key_envs"]
        default_model = preset["default_model"]
    else:
        raise ValueError(
            f"unknown MODEL_PROVIDER={prov!r}, expected one of "
            f"{sorted([*PRESETS, 'custom'])}"
        )
    api_key = next((os.environ[k] for k in key_envs if os.environ.get(k)), "")
    if not api_key:
        return None
    # Generic name on purpose: this is the OpenAI-compatible chat
    # client, not necessarily OpenAI itself (see MODEL_PROVIDER).
    from langchain_openai import ChatOpenAI as ChatModel

    return ChatModel(
        base_url=base_url,
        api_key=api_key,
        model=model or os.environ.get("COMPASS_MODEL")
        or os.environ.get("MODEL_NAME") or default_model,
    )


def resolve_model(spec="auto", role=None):
    """Resolve an agent model spec to a chat model, a passthrough, or None.

    - None -> None (caller uses its extractive fallback).
    - "auto" -> role default: MODEL_FOR_<ROLE> env, then the global
      COMPASS_MODEL / MODEL_NAME env, then AGENT_MODEL_DEFAULTS[role],
      then make_chat_model() defaults. Without a role it is pure env.
    - str -> make_chat_model(model=spec): a model id on the env provider.
    - dict -> make_chat_model(provider=d.get("provider"),
      model=d.get("model")).
    - anything else -> assumed LangChain chat instance, returned as-is.
    """
    if spec is None:
        return None
    if spec == "auto":
        if role:
            override = os.environ.get(_role_env(role))
            if override:
                return make_chat_model(model=override)
            if not (os.environ.get("COMPASS_MODEL")
                    or os.environ.get("MODEL_NAME")) \
                    and role in AGENT_MODEL_DEFAULTS:
                return make_chat_model(
                    model=AGENT_MODEL_DEFAULTS[role])
        return make_chat_model()
    if isinstance(spec, str):
        return make_chat_model(model=spec)
    if isinstance(spec, dict):
        return make_chat_model(provider=spec.get("provider"),
                               model=spec.get("model"))
    return spec
