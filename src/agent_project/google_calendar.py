"""Google Calendar and Google Tasks as the backend of CalendarTools.

Every visible calendar (selected in the Google Calendar UI) is read into memory
for a window around today, so search, free/busy, duplicate and conflict checks
are the same code as for the local file. Only writes call the API here.

Setup once: a Google Cloud OAuth "Desktop app" client with the Calendar API and
the Tasks API enabled, saved as workspace/credentials.json. The first run opens
a browser to sign in and caches the token in workspace/token.json.
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_project.tools import CalendarTools, Event, Task, ToolError

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]
CREDENTIALS = Path("workspace/credentials.json")
TOKEN = Path("workspace/token.json")
TIME_ZONE = "Europe/Warsaw"
WINDOW_DAYS = 180
WRITABLE_ROLES = ("owner", "writer")


def _pages(request_for, **params: Any) -> list[dict[str, Any]]:
    """All items of a paged Google list call."""
    items, token = [], None
    while True:
        page = request_for(**params, pageToken=token).execute()
        items.extend(page.get("items", []))
        token = page.get("nextPageToken")
        if not token:
            return items


def _local(value: dict[str, str]) -> datetime:
    """A Google start/end as the naive Europe/Warsaw time CalendarTools uses."""
    if "date" in value:
        return datetime.fromisoformat(value["date"])
    moment = datetime.fromisoformat(value["dateTime"])
    return moment.astimezone(ZoneInfo(TIME_ZONE)).replace(tzinfo=None)


def _event(item: dict[str, Any], calendar_name: str) -> Event:
    overrides = (item.get("reminders") or {}).get("overrides") or []
    reminders = tuple(
        int(o["minutes"]) for o in overrides if o.get("minutes") is not None
    )
    return Event(
        uid=f"gcal:{calendar_name}/{item['id']}",
        title=item.get("summary", ""),
        start=_local(item["start"]),
        end=_local(item["end"]),
        location=item.get("location", ""),
        description=item.get("description", ""),
        all_day="date" in item["start"],
        calendar=calendar_name,
        reminder_minutes=reminders,
    )


def _task(item: dict[str, Any], list_id: str, list_name: str) -> Task:
    due = item.get("due")  # RFC 3339; only the date part is meaningful
    return Task(
        uid=f"gtask:{list_id}/{item['id']}",
        title=item.get("title", ""),
        due=date.fromisoformat(due[:10]) if due else None,
        notes=item.get("notes", ""),
        completed=item.get("status") == "completed",
        task_list=list_name,
    )


def _task_ref(task: Task) -> tuple[str, str]:
    list_id, _, task_id = task.uid.removeprefix("gtask:").rpartition("/")
    return list_id, task_id


class GoogleCalendarTools(CalendarTools):
    def __init__(
        self,
        calendar_api: Any,
        tasks_api: Any,
        today: date | None = None,
        doc_index: Any | None = None,
    ) -> None:
        self.calendar_api, self.tasks_api = calendar_api, tasks_api
        today = today or date.today()
        start = datetime.combine(
            today - timedelta(days=WINDOW_DAYS), datetime.min.time()
        )
        end = datetime.combine(today + timedelta(days=WINDOW_DAYS), datetime.min.time())
        zone = ZoneInfo(TIME_ZONE)

        visible = [
            c
            for c in _pages(calendar_api.calendarList().list)
            if c.get("selected") and not c.get("hidden")
        ]
        self._calendar_ids: dict[str, str] = {}
        primary = ""
        self._all_calendar_ids: dict[str, str] = {}
        events = []
        for calendar in visible:
            name = calendar.get("summaryOverride") or calendar.get("summary", "")
            self._all_calendar_ids[name] = calendar["id"]
            if calendar.get("accessRole") in WRITABLE_ROLES:
                self._calendar_ids[name] = calendar["id"]
                if calendar.get("primary"):
                    primary = name
            items = _pages(
                calendar_api.events().list,
                calendarId=calendar["id"],
                timeMin=start.replace(tzinfo=zone).isoformat(),
                timeMax=end.replace(tzinfo=zone).isoformat(),
                singleEvents=True,
                maxResults=2500,
            )
            events += [
                _event(item, name)
                for item in items
                if item.get("status") != "cancelled" and "start" in item
            ]
        default_list = tasks_api.tasklists().get(tasklist="@default").execute()["id"]
        self._list_ids: dict[str, str] = {}
        tasks = []
        for task_list in _pages(tasks_api.tasklists().list, maxResults=100):
            name, list_id = task_list["title"], task_list["id"]
            if list_id == default_list:
                self._default_list = name
            self._list_ids[name] = list_id
            tasks += [
                _task(item, list_id, name)
                for item in _pages(
                    tasks_api.tasks().list,
                    tasklist=list_id,
                    showCompleted=True,
                    showHidden=True,
                    maxResults=100,
                )
            ]
        super().__init__(
            sorted(events, key=lambda e: e.start),
            doc_index=doc_index,
            tasks=tasks,
        )
        self.calendars = list(self._calendar_ids)
        # GOOGLE_DEFAULT_CALENDAR (a name) overrides your primary calendar.
        self.default_calendar = os.environ.get("GOOGLE_DEFAULT_CALENDAR") or primary
        if self.default_calendar and self.default_calendar not in self._calendar_ids:
            raise SystemExit(
                f"GOOGLE_DEFAULT_CALENDAR={self.default_calendar!r} is not a "
                f"writable calendar; choose one of {self.calendars}."
            )
        self.task_lists = list(self._list_ids)

    @classmethod
    def connect(cls, doc_index: Any | None = None) -> "GoogleCalendarTools":
        """Sign in (browser on the first run) and load every visible calendar."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise SystemExit(
                "Google backend needs google-api-python-client and "
                "google-auth-oauthlib: run `uv sync`."
            ) from exc
        if not CREDENTIALS.exists():
            raise SystemExit(
                f"Missing {CREDENTIALS}: download an OAuth Desktop client from "
                "Google Cloud Console (Calendar API and Tasks API enabled)."
            )
        creds = (
            Credentials.from_authorized_user_file(TOKEN, SCOPES)
            if TOKEN.exists()
            else None
        )
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
        return cls(
            build("calendar", "v3", credentials=creds),
            build("tasks", "v1", credentials=creds),
            doc_index=doc_index,
        )

    def _event_ref(self, event: Event) -> tuple[str, str]:
        cal_key, _, event_id = event.uid.removeprefix("gcal:").rpartition("/")
        return self._all_calendar_ids.get(cal_key, cal_key), event_id

    def _insert_event(self, event: Event) -> Event:
        calendar_id = self._calendar_ids[event.calendar]
        reminders: dict[str, Any] = (
            {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": m} for m in event.reminder_minutes
                ],
            }
            if event.reminder_minutes
            else {"useDefault": True}
        )
        body = {
            "summary": event.title,
            "location": event.location,
            "start": {"dateTime": event.start.isoformat(), "timeZone": TIME_ZONE},
            "end": {"dateTime": event.end.isoformat(), "timeZone": TIME_ZONE},
            "reminders": reminders,
        }
        created = (
            self.calendar_api.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="none")
            .execute()
        )
        # Report what Google stored, so a dropped reminder is visible, not hidden.
        saved = _event(created, event.calendar)
        self.events.append(saved)
        self.events.sort(key=lambda e: e.start)
        return saved

    def _remove_event(self, event: Event) -> None:
        calendar_id, event_id = self._event_ref(event)
        if calendar_id not in self._calendar_ids.values():
            raise ToolError(
                "read_only_calendar",
                f"Calendar '{event.calendar}' is read-only; nothing was deleted.",
            )
        self.calendar_api.events().delete(
            calendarId=calendar_id, eventId=event_id, sendUpdates="none"
        ).execute()
        self.events.remove(event)

    def _insert_task(self, task: Task) -> Task:
        body: dict[str, Any] = {"title": task.title, "notes": task.notes}
        if task.due:
            body["due"] = f"{task.due.isoformat()}T00:00:00.000Z"
        name = task.task_list or self._default_list
        list_id = self._list_ids[name]
        created = self.tasks_api.tasks().insert(tasklist=list_id, body=body).execute()
        saved = _task(created, list_id, name)
        self.tasks.append(saved)
        return saved

    def _complete_task(self, task: Task) -> Task:
        list_id, task_id = _task_ref(task)
        updated = (
            self.tasks_api.tasks()
            .patch(tasklist=list_id, task=task_id, body={"status": "completed"})
            .execute()
        )
        done = _task(updated, list_id, task.task_list)
        self.tasks[self.tasks.index(task)] = done
        return done

    def _remove_task(self, task: Task) -> None:
        list_id, task_id = _task_ref(task)
        self.tasks_api.tasks().delete(tasklist=list_id, task=task_id).execute()
        self.tasks.remove(task)

    def _move_task(self, task: Task, task_list: str) -> Task:
        list_id, task_id = _task_ref(task)
        target = self._list_ids[task_list]
        moved = (
            self.tasks_api.tasks()
            .move(tasklist=list_id, task=task_id, destinationTasklist=target)
            .execute()
        )
        saved = _task(moved, target, task_list)
        self.tasks[self.tasks.index(task)] = saved
        return saved
