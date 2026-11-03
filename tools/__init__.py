"""Service tools for agentic loops: plain functions + LangChain wrappers.

Each builder takes a service base URL and returns {name: spec} with
description + func. The brain calls funcs directly;
as_structured() converts a registry for LangChain agents. Every
function fails soft ({ok: False, ...}) so a model can reason about
errors instead of catching exceptions.
"""

from langchain_core.tools import StructuredTool


def as_structured(registry: dict) -> list:
    """Registry -> LangChain StructuredTools (names/descriptions kept)."""
    return [StructuredTool.from_function(
        func=spec["func"], name=name, description=spec["description"])
        for name, spec in registry.items()]
