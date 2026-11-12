"""The agent graph for LangGraph Studio: `uv run langgraph dev` (see langgraph.json).

The server calls make_graph for every run and keeps the conversations itself,
so the graph gets no checkpointer here. Settings come from calendar-agent.toml
and the calendar is CALENDAR_BACKEND from .env, else the CLI's working copy.
"""

import asyncio
import functools
from datetime import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from agent_project.agent import AGENT_TOOL_SCHEMAS, _Run, build_agent_graph
from agent_project.config import load_settings
from agent_project.llm import OpenRouterClient
from agent_project.tools import open_calendar


async def make_graph(config: RunnableConfig):
    # Building reads .env, the TOML and the calendar: blocking I/O must not run
    # on the server's event loop, so it runs in a worker thread.
    return await asyncio.to_thread(_build_graph)


@functools.cache
def _client(model: str, temperature: float) -> OpenRouterClient:
    # Studio asks for the graph on every request; a new client costs ~130 ms.
    return OpenRouterClient(model=model, temperature=temperature)


def _build_graph():
    settings = load_settings()
    return build_agent_graph(
        client=_client(settings.model.name, settings.model.temperature),
        tools=open_calendar(),
        # Trace only: Studio shows the run itself, so no run_end and no diagram.
        run_tracker=_Run(
            Path("traces", f"studio-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl")
        ),
        schemas=AGENT_TOOL_SCHEMAS,
        max_steps=settings.limits.max_steps,
        max_cost_usd=settings.limits.max_cost_usd,
        max_clarifications=settings.limits.max_clarifications,
        checkpointer=None,
        require_citations=settings.agent.require_citations,
    )
