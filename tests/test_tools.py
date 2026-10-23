from pathlib import Path

import pytest

from agent_project.tools import CalendarTools, execute_tool_call, tool_schemas

CALENDAR = Path(__file__).parents[1] / "data" / "calendar.ics"


@pytest.fixture(scope="module")
def tools() -> CalendarTools:
    return CalendarTools.from_file(CALENDAR)


def test_calendar_loads_timed_and_all_day_events(tools) -> None:
    assert len(tools.events) == 22
    birthday = next(e for e in tools.events if e.title == "Urodziny mamy")
    assert birthday.all_day
    assert (birthday.end - birthday.start).days == 1


def test_search_ignores_case_and_diacritics(tools) -> None:
    result = execute_tool_call(tools, "search_events", {"query": "wyklad KRYPTOGRAFIA"})
    assert result["ok"]
    assert result["result"]["total_matches"] == 4


def test_search_newest_first_with_date_range(tools) -> None:
    result = execute_tool_call(
        tools,
        "search_events",
        '{"query": "dentysta", "end": "2026-10-16", "order": "newest_first", '
        '"limit": 1}',
    )
    (event,) = result["result"]["events"]
    assert event["start"] == "2026-09-24T08:30"
    assert event["duration_min"] == 45
    assert result["result"]["total_matches"] == 3


def test_search_without_matches_is_not_an_error(tools) -> None:
    result = execute_tool_call(tools, "search_events", {"query": "dentist"})
    assert result["ok"]
    assert result["result"]["total_matches"] == 0


def test_search_requires_all_keywords(tools) -> None:
    # "Poznań" alone matches many locations; the meetup itself is not in the calendar
    result = execute_tool_call(tools, "search_events", {"query": "Python Poznań"})
    assert result["result"]["total_matches"] == 0


def test_free_busy_reports_conflict_and_free_gap(tools) -> None:
    result = execute_tool_call(
        tools, "free_busy", {"start": "2026-10-23T18:00", "end": "2026-10-23T20:00"}
    )["result"]
    assert not result["is_free"]
    assert [e["id"] for e in result["busy"]] == ["evt-018@calendar.local"]
    assert result["free_slots"] == [
        {"start": "2026-10-23T19:00", "end": "2026-10-23T20:00"}
    ]


def test_free_busy_ignores_all_day_events(tools) -> None:
    result = execute_tool_call(
        tools, "free_busy", {"start": "2026-11-04T08:00", "end": "2026-11-04T20:00"}
    )["result"]
    assert result["is_free"]
    assert result["busy"][0]["all_day"]


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("delete_event", {}, "unknown_tool"),
        ("search_events", "{not json", "invalid_json"),
        ("search_events", "[1, 2]", "invalid_json"),
        ("search_events", {}, "invalid_arguments"),
        ("search_events", {"query": "x", "limit": 500}, "invalid_arguments"),
        (
            "free_busy",
            {"start": "2026-10-23T20:00", "end": "2026-10-23T18:00"},
            "invalid_arguments",
        ),
        (
            "free_busy",
            {"start": "2026-10-01T00:00", "end": "2026-12-01T00:00"},
            "invalid_arguments",
        ),
    ],
)
def test_invalid_requests_are_rejected(tools, name, arguments, code) -> None:
    result = execute_tool_call(tools, name, arguments)
    assert not result["ok"]
    assert result["error"]["code"] == code


def test_tool_schemas_are_function_definitions() -> None:
    names = [schema["function"]["name"] for schema in tool_schemas()]
    assert names == ["search_events", "free_busy"]
    assert "query" in tool_schemas()[0]["function"]["parameters"]["required"]
