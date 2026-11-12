from datetime import date, datetime
from unittest.mock import MagicMock

from agent_project.google_calendar import GoogleCalendarTools
from agent_project.tools import execute_tool_call

CALENDARS = [
    {
        "id": "me@gmail.com",
        "summary": "me@gmail.com",
        "summaryOverride": "Prywatne",
        "accessRole": "owner",
        "selected": True,
    },
    {
        "id": "fam@group.calendar.google.com",
        "summary": "Rodzina",
        "accessRole": "writer",
        "selected": True,
    },
    {"id": "pl#holiday", "summary": "Święta", "accessRole": "reader", "selected": True},
    {
        "id": "off@group.calendar.google.com",
        "summary": "Ukryty",
        "accessRole": "owner",
        "selected": False,
    },
]
EVENTS = {
    "me@gmail.com": [
        # 16:00 UTC is 17:00 in Warsaw in November.
        {
            "id": "e1",
            "summary": "Dentysta",
            "start": {"dateTime": "2026-11-13T16:00:00Z"},
            "end": {"dateTime": "2026-11-13T17:00:00Z"},
        },
        {"id": "gone", "status": "cancelled"},
    ],
    "fam@group.calendar.google.com": [
        {
            "id": "e2",
            "summary": "Obiad u babci",
            "start": {"dateTime": "2026-11-15T13:00:00+01:00"},
            "end": {"dateTime": "2026-11-15T15:00:00+01:00"},
        },
    ],
    "pl#holiday": [
        {
            "id": "h1",
            "summary": "Narodowe Święto Niepodległości",
            "start": {"date": "2026-11-11"},
            "end": {"date": "2026-11-12"},
        },
    ],
}


def _apis():
    calendar_api, tasks_api = MagicMock(), MagicMock()
    calendar_api.calendarList.return_value.list.return_value.execute.return_value = {
        "items": CALENDARS
    }
    calendar_api.events.return_value.list.side_effect = lambda calendarId, **_: (
        MagicMock(execute=MagicMock(return_value={"items": EVENTS.get(calendarId, [])}))
    )
    calendar_api.events.return_value.insert.return_value.execute.return_value = {
        "id": "new1",
        "summary": "Kino",
        "start": {"dateTime": "2026-11-14T18:00:00+01:00"},
        "end": {"dateTime": "2026-11-14T20:00:00+01:00"},
        # The real API echoes the stored reminders back.
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 1440},
                {"method": "popup", "minutes": 30},
            ],
        },
    }
    tasks_api.tasklists.return_value.get.return_value.execute.return_value = {
        "id": "L1"
    }
    tasks_api.tasklists.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "L1", "title": "Moje zadania"},
            {"id": "L2", "title": "Codzienne"},
        ]
    }
    milk = {
        "id": "t1",
        "title": "Kupić mleko",
        "due": "2026-11-12T00:00:00.000Z",
        "status": "needsAction",
    }
    tasks_api.tasks.return_value.list.side_effect = lambda tasklist, **_: MagicMock(
        execute=MagicMock(return_value={"items": [milk] if tasklist == "L1" else []})
    )
    return calendar_api, tasks_api


def test_reads_visible_calendars_as_naive_warsaw_times() -> None:
    tools = GoogleCalendarTools(*_apis(), today=date(2026, 11, 12))
    # Holidays are read-only; the deselected "Ukryty" stays writable, not read.
    assert tools.calendars == ["Prywatne", "Rodzina", "Ukryty"]
    dentist = next(e for e in tools.events if e.title == "Dentysta")
    assert dentist.uid == "gcal:Prywatne/e1"
    assert dentist.start == datetime(2026, 11, 13, 17, 0)
    assert dentist.calendar == "Prywatne"
    assert "Ukryty" not in {e.calendar for e in tools.events}
    assert len(tools.events) == 3  # cancelled one skipped
    assert tools.tasks[0].uid == "gtask:L1/t1" and tools.tasks[0].due == date(
        2026, 11, 12
    )


def test_create_event_writes_to_the_named_calendar_without_emails() -> None:
    calendar_api, tasks_api = _apis()
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    result = execute_tool_call(
        tools,
        "create_event",
        {
            "title": "Kino",
            "start": "2026-11-14T18:00",
            "end": "2026-11-14T20:00",
            "calendar": "rodzina",
            "reminder_minutes": [1440, 30],
        },
        user_text="dodaj kino w sobotę o 18 do kalendarza Rodzina",
    )
    insert = calendar_api.events.return_value.insert
    kwargs = insert.call_args.kwargs
    assert kwargs["calendarId"] == "fam@group.calendar.google.com"
    assert kwargs["sendUpdates"] == "none"
    assert kwargs["body"]["start"] == {
        "dateTime": "2026-11-14T18:00:00",
        "timeZone": "Europe/Warsaw",
    }
    assert kwargs["body"]["reminders"] == {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": 1440},
            {"method": "popup", "minutes": 30},
        ],
    }
    created = result["result"]["created"]
    assert created["id"] == "gcal:Rodzina/new1"
    assert created["calendar"] == "Rodzina"
    assert created["reminder_minutes"] == [1440, 30]


def test_delete_event_and_complete_task_call_the_api() -> None:
    calendar_api, tasks_api = _apis()
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    tasks_api.tasks.return_value.patch.return_value.execute.return_value = {
        "id": "t1",
        "title": "Kupić mleko",
        "status": "completed",
    }
    deleted = execute_tool_call(
        tools, "delete_event", {"id": "gcal:Prywatne/e1"}, confirm=lambda q: True
    )
    assert deleted["ok"]
    calendar_api.events.return_value.delete.assert_called_once_with(
        calendarId="me@gmail.com", eventId="e1", sendUpdates="none"
    )
    done = execute_tool_call(tools, "complete_task", {"id": "gtask:L1/t1"})
    assert done["result"]["completed"]["completed"] is True
    assert tasks_api.tasks.return_value.patch.call_args.kwargs["task"] == "t1"


def test_read_only_calendar_event_is_not_deleted() -> None:
    calendar_api, tasks_api = _apis()
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    result = execute_tool_call(
        tools, "delete_event", {"id": "gcal:Święta/h1"}, confirm=lambda q: True
    )
    assert result["error"]["code"] == "read_only_calendar"
    calendar_api.events.return_value.delete.assert_not_called()


def test_tasks_use_every_list_and_move_between_them() -> None:
    calendar_api, tasks_api = _apis()
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    assert tools.task_lists == ["Moje zadania", "Codzienne"]
    assert tools.tasks[0].task_list == "Moje zadania"

    tasks_api.tasks.return_value.insert.return_value.execute.return_value = {
        "id": "t2",
        "title": "Skorupki",
        "status": "needsAction",
    }
    created = execute_tool_call(
        tools, "create_task", {"title": "Skorupki", "task_list": "codzienne"}
    )
    assert tasks_api.tasks.return_value.insert.call_args.kwargs["tasklist"] == "L2"
    assert created["result"]["created"]["id"] == "gtask:L2/t2"

    tasks_api.tasks.return_value.move.return_value.execute.return_value = {
        "id": "t1",
        "title": "Kupić mleko",
        "status": "needsAction",
    }
    moved = execute_tool_call(
        tools, "move_task", {"id": "gtask:L1/t1", "task_list": "Codzienne"}
    )
    tasks_api.tasks.return_value.move.assert_called_once_with(
        tasklist="L1", task="t1", destinationTasklist="L2"
    )
    assert moved["result"]["moved"]["task_list"] == "Codzienne"
    daily = execute_tool_call(tools, "list_tasks", {"task_list": "Codzienne"})
    assert [t["title"] for t in daily["result"]["tasks"]] == ["Kupić mleko", "Skorupki"]
    missing = execute_tool_call(
        tools, "move_task", {"id": "gtask:L2/t1", "task_list": "Praca"}
    )
    assert missing["error"]["code"] == "task_list_not_found"


def test_primary_calendar_is_the_default_unless_env_names_another(monkeypatch):
    import pytest

    calendar_api, tasks_api = _apis()
    listing = calendar_api.calendarList.return_value.list.return_value.execute
    listing.return_value = {
        "items": [{**CALENDARS[0], "primary": True}, *CALENDARS[1:]]
    }
    monkeypatch.delenv("GOOGLE_DEFAULT_CALENDAR", raising=False)
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    created = execute_tool_call(
        tools,
        "create_event",
        {"title": "Kino", "start": "2026-11-14T18:00", "end": "2026-11-14T20:00"},
        user_text="dodaj kino w sobotę o 18",
    )
    insert = calendar_api.events.return_value.insert
    assert insert.call_args.kwargs["calendarId"] == "me@gmail.com"
    assert created["result"]["created"]["calendar"] == "Prywatne"

    monkeypatch.setenv("GOOGLE_DEFAULT_CALENDAR", "Rodzina")
    assert GoogleCalendarTools(*_apis()).default_calendar == "Rodzina"
    monkeypatch.setenv("GOOGLE_DEFAULT_CALENDAR", "Praca")
    with pytest.raises(SystemExit, match="not a writable calendar"):
        GoogleCalendarTools(*_apis())


def test_duplicate_names_get_unique_identities_and_available_does_not_block():
    calendar_api, tasks_api = _apis()
    listing = calendar_api.calendarList.return_value.list.return_value.execute
    twin = {**CALENDARS[1], "id": "twin@group.calendar.google.com"}
    listing.return_value = {"items": [*CALENDARS, twin]}
    calendar_api.events.return_value.list.side_effect = lambda calendarId, **_: (
        MagicMock(
            execute=MagicMock(
                return_value={
                    "items": [
                        {
                            "id": "free1",
                            "summary": "Może kawa",
                            "transparency": "transparent",
                            "start": {"dateTime": "2026-11-16T10:00:00+01:00"},
                            "end": {"dateTime": "2026-11-16T11:00:00+01:00"},
                        }
                    ]
                    if calendarId == "twin@group.calendar.google.com"
                    else EVENTS.get(calendarId, [])
                }
            )
        )
    )
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    assert "Rodzina (twin@group.calendar.google.com)" in tools.calendars
    free = execute_tool_call(
        tools, "free_busy", {"start": "2026-11-16T09:00", "end": "2026-11-16T12:00"}
    )
    assert free["result"]["is_free"] is True


def test_days_outside_the_loaded_window_are_refused() -> None:
    tools = GoogleCalendarTools(*_apis(), today=date(2026, 11, 12))
    result = execute_tool_call(
        tools, "free_busy", {"start": "2028-01-10T09:00", "end": "2028-01-10T12:00"}
    )
    assert result["error"]["code"] == "outside_loaded_range"
