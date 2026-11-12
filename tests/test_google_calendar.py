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
    }
    tasks_api.tasks.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "t1",
                "title": "Kupić mleko",
                "due": "2026-11-12T00:00:00.000Z",
                "status": "needsAction",
            }
        ]
    }
    return calendar_api, tasks_api


def test_reads_visible_calendars_as_naive_warsaw_times() -> None:
    tools = GoogleCalendarTools(*_apis(), today=date(2026, 11, 12))
    assert tools.calendars == ["Prywatne", "Rodzina"]  # holidays are read-only
    dentist = next(e for e in tools.events if e.title == "Dentysta")
    assert dentist.uid == "gcal:me@gmail.com/e1"
    assert dentist.start == datetime(2026, 11, 13, 17, 0)
    assert dentist.calendar == "Prywatne"
    assert "Ukryty" not in {e.calendar for e in tools.events}
    assert len(tools.events) == 3  # cancelled one skipped
    assert tools.tasks[0].uid == "gtask:t1" and tools.tasks[0].due == date(2026, 11, 12)


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
    created = result["result"]["created"]
    assert created["id"] == "gcal:fam@group.calendar.google.com/new1"
    assert created["calendar"] == "Rodzina"


def test_delete_event_and_complete_task_call_the_api() -> None:
    calendar_api, tasks_api = _apis()
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    tasks_api.tasks.return_value.patch.return_value.execute.return_value = {
        "id": "t1",
        "title": "Kupić mleko",
        "status": "completed",
    }
    deleted = execute_tool_call(
        tools, "delete_event", {"id": "gcal:me@gmail.com/e1"}, confirm=lambda q: True
    )
    assert deleted["ok"]
    calendar_api.events.return_value.delete.assert_called_once_with(
        calendarId="me@gmail.com", eventId="e1", sendUpdates="none"
    )
    done = execute_tool_call(tools, "complete_task", {"id": "gtask:t1"})
    assert done["result"]["completed"]["completed"] is True
    assert tasks_api.tasks.return_value.patch.call_args.kwargs["task"] == "t1"


def test_read_only_calendar_event_is_not_deleted() -> None:
    calendar_api, tasks_api = _apis()
    tools = GoogleCalendarTools(calendar_api, tasks_api, today=date(2026, 11, 12))
    result = execute_tool_call(
        tools, "delete_event", {"id": "gcal:pl#holiday/h1"}, confirm=lambda q: True
    )
    assert result["error"]["code"] == "read_only_calendar"
    calendar_api.events.return_value.delete.assert_not_called()
