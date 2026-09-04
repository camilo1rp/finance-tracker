"""LangGraph Studio entrypoints. Side-effect-free import; graphs compile on demand.

The dev server owns persistence (in-memory; Studio threads vanish on restart).
The app checkpointer is unused on this path. This module is the only in-repo
caller of build_coordinator(checkpointer=None).
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.agent.analyst import build_analyst
from app.agent.coordinator import build_coordinator
from app.agent.enricher_graph import build_enricher_graph
from app.agent.steward_graph import build_steward_graph


def coordinator_graph(config: RunnableConfig | None = None):
    """Full coordinator for Studio. No compile-time checkpointer."""
    del config
    return build_coordinator(checkpointer=None)


def steward_graph(config: RunnableConfig | None = None):
    """Steward subgraph alone for Studio. No compile-time checkpointer."""
    del config
    return build_steward_graph(checkpointer=None)


def analyst_graph(config: RunnableConfig | None = None):
    """Analyst alone for Studio."""
    del config
    return build_analyst()


def enricher_graph(config: RunnableConfig | None = None):
    """Enricher alone for Studio. No compile-time checkpointer."""
    del config
    return build_enricher_graph()
