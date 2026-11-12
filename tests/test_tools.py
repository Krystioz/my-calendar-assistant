from pathlib import Path

import pytest

from agent_project.tools import (
    CalendarTools,
    execute_tool_call,
    named_in,
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
    assert names == [
        "search_events",
        "free_busy",
        "create_event",
        "delete_event",
        "list_tasks",
        "create_task",
        "complete_task",
        "move_task",
        "delete_task",
    ]
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


def test_tasks_live_in_the_working_copy_and_complete_or_delete(writable, tmp_path):
    created = execute_tool_call(
        writable,
        "create_task",
        {"title": "Zadzwonić do Kowalskiego", "due": "2026-10-20"},
    )
    task_id = created["result"]["created"]["id"]
    again = execute_tool_call(
        writable, "create_task", {"title": "zadzwonic do kowalskiego"}
    )
    assert again["error"]["code"] == "already_exists"

    due = execute_tool_call(writable, "list_tasks", {"due_by": "2026-10-20"})
    assert [t["id"] for t in due["result"]["tasks"]] == [task_id]
    assert (
        execute_tool_call(writable, "list_tasks", {"due_by": "2026-10-19"})["result"][
            "tasks"
        ]
        == []
    )

    execute_tool_call(writable, "complete_task", {"id": task_id})
    saved = CalendarTools.from_file(tmp_path / "calendar.ics")
    assert [t.completed for t in saved.tasks] == [True]
    assert len(saved.events) == 22  # tasks never become events
    assert execute_tool_call(saved, "list_tasks", {})["result"]["tasks"] == []

    refused = execute_tool_call(saved, "delete_task", {"id": task_id})
    assert refused["error"]["code"] == "confirmation_required"
    deleted = execute_tool_call(
        saved, "delete_task", {"id": task_id}, confirm=lambda q: True
    )
    assert (
        deleted["ok"] and CalendarTools.from_file(tmp_path / "calendar.ics").tasks == []
    )


@pytest.fixture
def two_calendars(writable) -> CalendarTools:
    writable.calendars = ["Prywatne", "Rodzina"]
    return writable


EVENT = {"title": "Dentysta", "start": "2026-11-02T10:00", "end": "2026-11-02T11:00"}


def test_create_event_needs_a_calendar_when_several_exist(two_calendars) -> None:
    missing = execute_tool_call(two_calendars, "create_event", EVENT)
    assert missing["error"]["code"] == "calendar_not_specified"
    assert missing["error"]["calendars"] == ["Prywatne", "Rodzina"]
    unknown = execute_tool_call(
        two_calendars, "create_event", {**EVENT, "calendar": "Praca"}
    )
    assert unknown["error"]["code"] == "calendar_not_found"


def test_calendar_the_user_never_named_is_rejected(two_calendars) -> None:
    args = {**EVENT, "calendar": "Rodzina"}
    guessed = execute_tool_call(
        two_calendars, "create_event", args, user_text="dodaj dentystę jutro o 10"
    )
    assert guessed["error"]["code"] == "calendar_not_specified"
    named = execute_tool_call(
        two_calendars, "create_event", args, user_text="dodaj w kalendarzu rodzinnym"
    )
    assert named["result"]["created"]["calendar"] == "Rodzina"


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("Prywatne", "wpisz do prywatnego", True),
        ("Personal tasks", "put it in my personal task calendar", True),
        ("Krysitan", "w kalendaruz krystian", True),
        ("Rodzina", "dodaj to do kalendarza", False),
        ("Personal tasks", "personal", False),
    ],
)
def test_named_in_matches_polish_word_forms_only(name, text, expected) -> None:
    assert named_in(name, text) is expected


def test_search_with_empty_query_lists_date_range_and_saves_reminder(
    writable, tmp_path
) -> None:
    listed = execute_tool_call(
        writable,
        "search_events",
        {"query": "", "start": "2026-10-19", "end": "2026-10-25", "limit": 20},
    )
    assert listed["ok"]
    assert listed["result"]["total_matches"] >= 5

    created = execute_tool_call(
        writable,
        "create_event",
        {
            "title": "Wizyta z przypomnieniem",
            "start": "2026-11-10T10:00",
            "end": "2026-11-10T10:00",
            "reminder_minutes": [1440, 60],
        },
    )
    assert created["ok"]
    assert created["result"]["created"]["end"] == "2026-11-10T10:30"
    assert created["result"]["created"]["reminder_minutes"] == [1440, 60]
    reloaded = CalendarTools.from_file(tmp_path / "calendar.ics")
    saved = next(e for e in reloaded.events if e.title == "Wizyta z przypomnieniem")
    assert saved.reminder_minutes == (1440, 60)


def test_guard_checks_the_resolved_calendar_not_the_raw_string(two_calendars):
    # Regression: "na" passed the guard (it is in the text) and resolved to Rodzina.
    sneaky = execute_tool_call(
        two_calendars,
        "create_event",
        {**EVENT, "calendar": "na"},
        user_text="dodaj dentystę na jutro o 10",
    )
    assert sneaky["ok"] is False
    assert not named_in("Praca", "Dodaj spotkanie z pracownikiem jutro o 10")
    assert named_in("Praca", "wpisz to do pracy")


def test_far_future_start_is_a_validation_error_not_a_crash(writable) -> None:
    result = execute_tool_call(
        writable, "create_event", {"title": "X", "start": "9999-12-31T23:59"}
    )
    assert result["error"]["code"] == "invalid_arguments"
