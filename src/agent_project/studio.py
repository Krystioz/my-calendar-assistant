"""The agent graph for the LangGraph server: `uv run langgraph dev` (langgraph.json).

The server backs both the chat UI in ui/ and LangGraph Studio. It calls
make_graph for every run and keeps the conversations itself, so the graph gets
no checkpointer here. Settings come from calendar-agent.toml. The calendar is
`configurable.calendar` ("local" or "google"), else CALENDAR_BACKEND from .env,
else the CLI's working copy.
"""

import asyncio
import functools
from datetime import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from agent_project.agent import AGENT_TOOL_SCHEMAS, _Run, build_agent_graph
from agent_project.config import load_settings
from agent_project.llm import OpenRouterClient
from agent_project.tools import WORKING_CALENDAR, open_calendar

# Only these names reach open_calendar: a client must not pick a file path.
CALENDARS = {"local": WORKING_CALENDAR, "google": "google"}


async def make_graph(config: RunnableConfig):
    # Building reads .env, the TOML and the calendar: blocking I/O must not run
    # on the server's event loop, so it runs in a worker thread.
    calendar = (config.get("configurable") or {}).get("calendar")
    if calendar is not None and calendar not in CALENDARS:
        raise ValueError(
            f"Unknown calendar {calendar!r}; use one of {list(CALENDARS)}."
        )
    return await asyncio.to_thread(_build_graph, CALENDARS.get(calendar))


@functools.cache
def _client(model: str, temperature: float) -> OpenRouterClient:
    # Studio asks for the graph on every request; a new client costs ~130 ms.
    return OpenRouterClient(model=model, temperature=temperature)


def _build_graph(calendar: str | Path | None):
    settings = load_settings()
    return build_agent_graph(
        client=_client(settings.model.name, settings.model.temperature),
        tools=open_calendar(calendar),
        # Trace only: Studio shows the run itself, so no run_end and no diagram.
        run_tracker=_Run(
            Path("traces", f"server-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl")
        ),
        schemas=AGENT_TOOL_SCHEMAS,
        max_steps=settings.limits.max_steps,
        max_cost_usd=settings.limits.max_cost_usd,
        max_clarifications=settings.limits.max_clarifications,
        checkpointer=None,
        require_citations=settings.agent.require_citations,
    )
