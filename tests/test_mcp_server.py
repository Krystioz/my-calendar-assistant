from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="run `uv sync` to install the MCP SDK")

from mcp import Client  # noqa: E402

from agent_project.mcp_server import build_server  # noqa: E402
from agent_project.tools import CalendarTools, working_copy  # noqa: E402

CALENDAR = Path(__file__).parents[1] / "data" / "calendar.ics"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(tmp_path):
    tools = CalendarTools.from_file(working_copy(CALENDAR, tmp_path / "calendar.ics"))
    return build_server(tools)


@pytest.mark.anyio
async def test_server_lists_the_same_calendar_and_task_tools(server) -> None:
    async with Client(server) as client:
        listed = await client.list_tools()
    assert sorted(tool.name for tool in listed.tools) == [
        "complete_task",
        "create_event",
        "create_task",
        "delete_event",
        "delete_task",
        "free_busy",
        "list_tasks",
        "move_task",
        "search_events",
    ]


@pytest.mark.anyio
async def test_search_and_duplicate_go_through_shared_checks(server) -> None:
    async with Client(server) as client:
        found = await client.call_tool("search_events", {"query": "dentysta"})
        duplicate = await client.call_tool(
            "create_event",
            {
                "title": "Siłownia",
                "start": "2026-10-19T18:00",
                "end": "2026-10-19T19:30",
            },
        )
    assert found.structured_content["result"]["total_matches"] == 3
    assert duplicate.structured_content["error"]["code"] == "already_exists"


@pytest.mark.anyio
async def test_delete_over_mcp_needs_confirmation_and_changes_nothing(
    server, tmp_path
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "delete_event", {"id": "evt-018@calendar.local"}
        )
    assert result.structured_content["error"]["code"] == "confirmation_required"
    ids = [e.uid for e in CalendarTools.from_file(tmp_path / "calendar.ics").events]
    assert "evt-018@calendar.local" in ids


@pytest.mark.anyio
async def test_mcp_tools_match_the_shared_tool_specs(server) -> None:
    # The wrappers repeat each signature; this catches drift when a tool changes.
    from agent_project.tools import TOOL_SPECS

    async with Client(server) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(listed) == {spec.name for spec in TOOL_SPECS}
    for spec in TOOL_SPECS:
        params = set(listed[spec.name].input_schema["properties"])
        assert params == set(spec.args_model.model_fields), spec.name
