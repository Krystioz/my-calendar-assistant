"""Google Calendar and Google Tasks as the backend of CalendarTools.

Every visible calendar (selected in the Google Calendar UI) is read into memory
for a window around today, so search, free/busy, duplicate and conflict checks
are the same code as for the local file. Only writes call the API here.

Setup once: a Google Cloud OAuth "Desktop app" client with the Calendar API and
the Tasks API enabled, saved as workspace/credentials.json. The first run opens
a browser to sign in and caches the token in workspace/token.json.
"""

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
TASK_LIST = "@default"
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


def _event(item: dict[str, Any], calendar_id: str, calendar_name: str) -> Event:
    return Event(
        uid=f"gcal:{calendar_id}/{item['id']}",
        title=item.get("summary", ""),
        start=_local(item["start"]),
        end=_local(item["end"]),
        location=item.get("location", ""),
        description=item.get("description", ""),
        all_day="date" in item["start"],
        calendar=calendar_name,
    )


def _task(item: dict[str, Any]) -> Task:
    due = item.get("due")  # RFC 3339; only the date part is meaningful
    return Task(
        uid=f"gtask:{item['id']}",
        title=item.get("title", ""),
        due=date.fromisoformat(due[:10]) if due else None,
        notes=item.get("notes", ""),
        completed=item.get("status") == "completed",
    )


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
        events = []
        for calendar in visible:
            name = calendar.get("summaryOverride") or calendar.get("summary", "")
            if calendar.get("accessRole") in WRITABLE_ROLES:
                self._calendar_ids[name] = calendar["id"]
            items = _pages(
                calendar_api.events().list,
                calendarId=calendar["id"],
                timeMin=start.replace(tzinfo=zone).isoformat(),
                timeMax=end.replace(tzinfo=zone).isoformat(),
                singleEvents=True,
                maxResults=2500,
            )
            events += [
                _event(item, calendar["id"], name)
                for item in items
                if item.get("status") != "cancelled" and "start" in item
            ]
        tasks = _pages(
            tasks_api.tasks().list,
            tasklist=TASK_LIST,
            showCompleted=True,
            showHidden=True,
            maxResults=100,
        )
        super().__init__(
            sorted(events, key=lambda e: e.start),
            doc_index=doc_index,
            tasks=[_task(item) for item in tasks],
        )
        self.calendars = list(self._calendar_ids)

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
        calendar_id, _, event_id = event.uid.removeprefix("gcal:").rpartition("/")
        return calendar_id, event_id

    def _insert_event(self, event: Event) -> Event:
        calendar_id = self._calendar_ids[event.calendar]
        body = {
            "summary": event.title,
            "location": event.location,
            "start": {"dateTime": event.start.isoformat(), "timeZone": TIME_ZONE},
            "end": {"dateTime": event.end.isoformat(), "timeZone": TIME_ZONE},
        }
        created = (
            self.calendar_api.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="none")
            .execute()
        )
        saved = _event(created, calendar_id, event.calendar)
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
        saved = _task(
            self.tasks_api.tasks().insert(tasklist=TASK_LIST, body=body).execute()
        )
        self.tasks.append(saved)
        return saved

    def _complete_task(self, task: Task) -> Task:
        updated = (
            self.tasks_api.tasks()
            .patch(
                tasklist=TASK_LIST,
                task=task.uid.removeprefix("gtask:"),
                body={"status": "completed"},
            )
            .execute()
        )
        done = _task(updated)
        self.tasks[self.tasks.index(task)] = done
        return done

    def _remove_task(self, task: Task) -> None:
        self.tasks_api.tasks().delete(
            tasklist=TASK_LIST, task=task.uid.removeprefix("gtask:")
        ).execute()
        self.tasks.remove(task)
