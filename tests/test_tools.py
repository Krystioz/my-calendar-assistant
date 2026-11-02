from pathlib import Path

import pytest

from agent_project.tools import (
    CalendarTools,
    execute_tool_call,
    tool_schemas,
    working_copy,
)

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
        ("send_invitation", {}, "unknown_tool"),
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
    assert names == ["search_events", "free_busy", "create_event", "delete_event"]
    assert "query" in tool_schemas()[0]["function"]["parameters"]["required"]


@pytest.fixture
def writable(tmp_path) -> CalendarTools:
    return CalendarTools.from_file(working_copy(CALENDAR, tmp_path / "calendar.ics"))


def test_working_copy_leaves_the_source_untouched(tmp_path, writable) -> None:
    before = CALENDAR.read_bytes()
    execute_tool_call(
        writable,
        "create_event",
        {"title": "Test", "start": "2026-11-01T10:00", "end": "2026-11-01T11:00"},
    )
    assert CALENDAR.read_bytes() == before
    saved = CalendarTools.from_file(tmp_path / "calendar.ics")
    assert any(e.title == "Test" for e in saved.events)


def test_create_event_reports_conflicts(writable) -> None:
    result = execute_tool_call(
        writable,
        "create_event",
        {
            "title": "Python Meetup",
            "start": "2026-10-23T18:00",
            "end": "2026-10-23T20:00",
        },
    )
    assert result["ok"]
    assert result["result"]["created"]["id"].startswith("agent-")
    assert [c["id"] for c in result["result"]["conflicts"]] == [
        "evt-018@calendar.local"
    ]


def test_create_event_rejects_duplicate(writable) -> None:
    result = execute_tool_call(
        writable,
        "create_event",
        {"title": "SILOWNIA", "start": "2026-10-19T18:00", "end": "2026-10-19T19:00"},
    )
    assert result["error"]["code"] == "already_exists"
    assert result["error"]["existing"]["id"] == "evt-013@calendar.local"


@pytest.mark.parametrize(
    "arguments",
    [
        {"title": "", "start": "2026-11-01T10:00", "end": "2026-11-01T11:00"},
        {"title": "X", "start": "2026-11-01T11:00", "end": "2026-11-01T10:00"},
        {"title": "X", "start": "2026-11-01T10:00", "end": "2026-11-03T10:00"},
    ],
)
def test_create_event_validates_arguments(writable, arguments) -> None:
    result = execute_tool_call(writable, "create_event", arguments)
    assert result["error"]["code"] == "invalid_arguments"


def test_delete_unknown_event_is_not_found_before_asking(writable) -> None:
    asked = []
    result = execute_tool_call(
        writable, "delete_event", {"id": "nope"}, confirm=asked.append
    )
    assert result["error"]["code"] == "not_found"
    assert asked == []


def test_create_event_catches_translated_duplicate_in_same_slot(writable) -> None:
    # Regression: a live run added "Gym" next to "Siłownia" (evidence/m2-*).
    result = execute_tool_call(
        writable,
        "create_event",
        {"title": "Gym", "start": "2026-10-19T18:00", "end": "2026-10-19T19:30"},
    )
    assert result["error"]["code"] == "already_exists"
    assert result["error"]["existing"]["title"] == "Siłownia"


def test_time_zone_suffix_from_the_model_is_treated_as_local(writable) -> None:
    # Regression: "...T18:00:00Z" from a live run crashed the naive comparison.
    result = execute_tool_call(
        writable,
        "create_event",
        {
            "title": "Python Meetup",
            "start": "2026-10-23T18:00:00Z",
            "end": "2026-10-23T20:00:00Z",
        },
    )
    assert result["ok"]
    assert result["result"]["created"]["start"] == "2026-10-23T18:00"


@pytest.mark.parametrize("query", ["*|*", "**", "dentysta *"])
def test_wildcard_query_is_rejected_with_free_busy_hint(tools, query) -> None:
    # Regression: a live run searched "*|*" and answered "no events that day".
    result = execute_tool_call(tools, "search_events", {"query": query})
    assert result["error"]["code"] == "invalid_arguments"
    assert "free_busy" in result["error"]["message"]
