"""Read-only HTTP routes the chat UI needs beside the agent (langgraph.json "http").

langgraph-api mounts `app` next to its own routes and wraps it in its CORS
middleware, so the routes here only read: the calendar, one doc chunk, limits.
"""

import asyncio
import functools
from datetime import date, datetime, time, timedelta

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, request_response

from agent_project import studio
from agent_project.config import load_settings
from agent_project.rag import DocumentIndex

MAX_RANGE_DAYS = 62
UI_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


async def calendar(request: Request) -> JSONResponse:
    """GET /calendar?calendar=local|google&start=YYYY-MM-DD&end=YYYY-MM-DD."""
    name = request.query_params.get("calendar", "local")
    # Only these names reach open_calendar: a client must not pick a file path.
    if name not in studio.CALENDARS:
        return _bad_request(f"Unknown calendar; use one of {list(studio.CALENDARS)}.")
    try:
        start = date.fromisoformat(request.query_params["start"])
        end = date.fromisoformat(request.query_params["end"])
    except (KeyError, ValueError):
        return _bad_request("start and end must be YYYY-MM-DD dates.")
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        return _bad_request(f"Need start <= end, at most {MAX_RANGE_DAYS} days apart.")

    try:
        tools = await asyncio.to_thread(studio.open_calendar, studio.CALENDARS[name])
    except SystemExit as exc:  # GoogleStore.connect exits on missing credentials
        return JSONResponse({"error": str(exc)}, status_code=503)
    # Half-open overlap with [start 00:00, end+1 00:00): all-day ends are exclusive.
    first = datetime.combine(start, time())
    after = datetime.combine(end + timedelta(days=1), time())
    return JSONResponse(
        {
            "events": [
                e.to_dict() for e in tools.events if e.start < after and e.end > first
            ],
            "tasks": [t.to_dict() for t in tools.tasks],
            "calendars": tools.calendars,
        }
    )


@functools.cache
def _doc_index() -> DocumentIndex:
    return DocumentIndex.load_or_build()


async def doc_chunk(request: Request) -> JSONResponse:
    """GET /docs?id=<chunk id>: one document fragment the agent cited."""
    chunk_id = request.query_params.get("id", "")
    index = await asyncio.to_thread(_doc_index)
    chunk = next((c for c in index.chunks if c.id == chunk_id), None)
    if chunk is None:
        return JSONResponse({"error": f"No chunk {chunk_id!r}."}, status_code=404)
    return JSONResponse(
        {
            "id": chunk.id,
            "source": chunk.source,
            "heading": chunk.heading,
            "text": chunk.text,
        }
    )


async def limits(request: Request) -> JSONResponse:
    """GET /limits: the [limits] section of calendar-agent.toml."""
    settings = await asyncio.to_thread(load_settings)
    return JSONResponse(settings.limits.model_dump())


class _DocChunks:
    """Answer GET /docs?id=... before routing reaches langgraph-api's /docs.

    langgraph-api keeps its own API docs page at /docs above custom routes, and
    custom middleware runs outside its CORS middleware, so this adds CORS itself.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.chunks = CORSMiddleware(
            request_response(doc_chunk), allow_origins=UI_ORIGINS, allow_methods=["GET"]
        )

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"] == "/docs"
            and "id" in Request(scope).query_params
        ):
            await self.chunks(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app = Starlette(
    routes=[Route("/calendar", calendar), Route("/limits", limits)],
    middleware=[Middleware(_DocChunks)],
)
