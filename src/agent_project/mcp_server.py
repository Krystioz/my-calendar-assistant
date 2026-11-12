"""Local MCP server exposing the same calendar tools the agent uses.

Every tool forwards to ``execute_tool_call``, so validation, the duplicate check
and the confirmation rule are shared with the agent, not implemented twice.
MCP has no channel to ask the user here, so ``delete_event`` and ``delete_task``
always return confirmation_required and change nothing.

Run it over stdio: ``uv run calendar-mcp``.
"""

from typing import Any

from mcp.server import MCPServer

from agent_project.tools import (
    TOOL_SPECS,
    CalendarTools,
    execute_tool_call,
    open_calendar,
)

_DESCRIPTIONS = {spec.name: spec.description for spec in TOOL_SPECS}


def build_server(tools: CalendarTools) -> MCPServer:
    server = MCPServer("calendar-assistant")

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # Unset optional arguments fall back to the Pydantic defaults.
        given = {key: value for key, value in arguments.items() if value is not None}
        return execute_tool_call(tools, name, given, confirm=None)

    def search_events(
        query: str,
        start: str | None = None,
        end: str | None = None,
        order: str | None = None,
        limit: int | None = None,
        calendar: str | None = None,
    ) -> dict[str, Any]:
        return call("search_events", locals())

    def free_busy(start: str, end: str) -> dict[str, Any]:
        return call("free_busy", locals())

    def create_event(
        title: str,
        start: str,
        end: str,
        location: str | None = None,
        calendar: str | None = None,
    ) -> dict[str, Any]:
        return call("create_event", locals())

    def delete_event(id: str) -> dict[str, Any]:
        return call("delete_event", locals())

    def list_tasks(
        query: str | None = None,
        due_by: str | None = None,
        include_completed: bool | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return call("list_tasks", locals())

    def create_task(
        title: str, due: str | None = None, notes: str | None = None
    ) -> dict[str, Any]:
        return call("create_task", locals())

    def complete_task(id: str) -> dict[str, Any]:
        return call("complete_task", locals())

    def delete_task(id: str) -> dict[str, Any]:
        return call("delete_task", locals())

    for function in (
        search_events,
        free_busy,
        create_event,
        delete_event,
        list_tasks,
        create_task,
        complete_task,
        delete_task,
    ):
        function.__doc__ = _DESCRIPTIONS[function.__name__]
        server.tool()(function)
    return server


def main() -> None:
    build_server(open_calendar()).run()


if __name__ == "__main__":
    main()
