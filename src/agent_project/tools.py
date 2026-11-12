"""Calendar and task tools and the code-side checks for every tool request.

The agent works on a local working copy of the calendar, so the source file in
data/ never changes, or on Google Calendar and Google Tasks (google_calendar.py).
All times are local wall-clock times (no time zones).
"""

import json
import os
import shutil
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import icalendar
from dotenv import load_dotenv
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    ValidationError,
    model_validator,
)

MAX_RESULTS = 20
MAX_FREE_BUSY_DAYS = 14
MAX_EVENT_HOURS = 24
SOURCE_CALENDAR = Path("data/calendar.ics")
WORKING_CALENDAR = Path("workspace/calendar.ics")

# Asked by the application, never by the model: returns True only if a human agreed.
Confirm = Callable[[str], bool]

# The calendar is wall-clock local time; a model-added offset such as "Z" is dropped
# so it cannot crash comparisons with the naive times in the calendar.
LocalDateTime = Annotated[datetime, AfterValidator(lambda v: v.replace(tzinfo=None))]


class ToolError(Exception):
    """A domain outcome the model should read, such as already_exists."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    all_day: bool = False
    calendar: str = ""  # display name; empty for the local file

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.uid,
            **({"calendar": self.calendar} if self.calendar else {}),
            "title": self.title,
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes"),
            "duration_min": int((self.end - self.start).total_seconds() // 60),
            "all_day": self.all_day,
            "location": self.location,
            "description": self.description,
        }

    def to_ical(self) -> icalendar.Event:
        item = icalendar.Event()
        item.add("uid", self.uid)
        item.add("dtstamp", datetime.now(UTC))
        item.add("summary", self.title)
        item.add("dtstart", self.start.date() if self.all_day else self.start)
        item.add("dtend", self.end.date() if self.all_day else self.end)
        if self.location:
            item.add("location", self.location)
        if self.description:
            item.add("description", self.description)
        return item


@dataclass(frozen=True)
class Task:
    uid: str
    title: str
    due: date | None = None  # Google Tasks keeps only the date of a due time
    notes: str = ""
    completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.uid,
            "title": self.title,
            "due": self.due.isoformat() if self.due else None,
            "notes": self.notes,
            "completed": self.completed,
        }

    def to_ical(self) -> icalendar.Todo:
        item = icalendar.Todo()
        item.add("uid", self.uid)
        item.add("dtstamp", datetime.now(UTC))
        item.add("summary", self.title)
        item.add("status", "COMPLETED" if self.completed else "NEEDS-ACTION")
        if self.due:
            item.add("due", self.due)
        if self.notes:
            item.add("description", self.notes)
        return item


def working_copy(
    source: Path = SOURCE_CALENDAR, target: Path = WORKING_CALENDAR
) -> Path:
    """Return the working calendar, copying the source the first time."""
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return target


def save_events(path: Path, events: list[Event], tasks: list[Task] = ()) -> None:
    calendar = icalendar.Calendar()
    calendar.add("prodid", "-//my-calendar-assistant//agent working copy//EN")
    calendar.add("version", "2.0")
    for item in [*events, *tasks]:
        calendar.add_component(item.to_ical())
    path.write_bytes(calendar.to_ical())


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.combine(value, datetime.min.time())


def load_events(path: Path) -> list[Event]:
    calendar = icalendar.Calendar.from_ical(path.read_bytes())
    events = []
    for item in calendar.walk("VEVENT"):
        raw_start = item.decoded("DTSTART")
        all_day = not isinstance(raw_start, datetime)
        start = _as_datetime(raw_start)
        if "DTEND" in item:
            end = _as_datetime(item.decoded("DTEND"))
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
        events.append(
            Event(
                uid=str(item.get("UID", "")),
                title=str(item.get("SUMMARY", "")),
                start=start,
                end=end,
                location=str(item.get("LOCATION", "")),
                description=str(item.get("DESCRIPTION", "")),
                all_day=all_day,
            )
        )
    return sorted(events, key=lambda event: event.start)


def load_tasks(path: Path) -> list[Task]:
    calendar = icalendar.Calendar.from_ical(path.read_bytes())
    return [
        Task(
            uid=str(item.get("UID", "")),
            title=str(item.get("SUMMARY", "")),
            due=_as_datetime(item.decoded("DUE")).date() if "DUE" in item else None,
            notes=str(item.get("DESCRIPTION", "")),
            completed=str(item.get("STATUS", "")).upper() == "COMPLETED",
        )
        for item in calendar.walk("VTODO")
    ]


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so 'Wykład' matches 'wyklad'."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def named_in(name: str, text: str) -> bool:
    """True if every word of the calendar name appears in text.

    shortcut: a word matches by its stem (all but the last 2 letters, at least 4),
    so Polish forms ("prywatnym" for "Prywatne") pass but nicknames do not;
    upgrade to an alias list if users name calendars by nicknames.
    """
    folded = _fold(text)
    return all(_fold(word)[: max(4, len(word) - 2)] in folded for word in name.split())


class SearchEventsArgs(BaseModel):
    """Find events whose title, location or description contains a keyword."""

    query: str = Field(
        min_length=2,
        max_length=100,
        description="One or two keywords; an event matches only if it contains "
        "all of them. Calendar entries are mostly in Polish.",
    )
    start: date | None = Field(None, description="Earliest day, YYYY-MM-DD.")
    end: date | None = Field(None, description="Latest day (inclusive), YYYY-MM-DD.")
    order: Literal["oldest_first", "newest_first"] = "oldest_first"
    limit: int = Field(10, ge=1, le=MAX_RESULTS)
    calendar: str | None = Field(
        None, max_length=100, description="Only events of this calendar (by name)."
    )

    @model_validator(mode="after")
    def _check_range(self) -> "SearchEventsArgs":
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must not be after end")
        # A live run sent "*|*" and read the empty result as "no events that day".
        if any(not any(ch.isalnum() for ch in word) for word in self.query.split()):
            raise ValueError(
                "query takes plain keywords, not wildcards or symbols; to list "
                "everything on a day, call free_busy for that day instead"
            )
        return self


class FreeBusyArgs(BaseModel):
    """List busy events and free gaps between start and end."""

    start: LocalDateTime = Field(description="Window start, YYYY-MM-DDTHH:MM.")
    end: LocalDateTime = Field(description="Window end, YYYY-MM-DDTHH:MM.")

    @model_validator(mode="after")
    def _check_range(self) -> "FreeBusyArgs":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if self.end - self.start > timedelta(days=MAX_FREE_BUSY_DAYS):
            raise ValueError(f"window must be at most {MAX_FREE_BUSY_DAYS} days")
        return self


class CreateEventArgs(BaseModel):
    """Add one event to the calendar."""

    title: str = Field(min_length=1, max_length=200)
    start: LocalDateTime = Field(description="Event start, YYYY-MM-DDTHH:MM.")
    end: LocalDateTime = Field(description="Event end, YYYY-MM-DDTHH:MM.")
    location: str = Field("", max_length=200)
    calendar: str | None = Field(
        None,
        max_length=100,
        description="Name of the calendar to add it to, as the user said it. "
        "When the user did not name one, do not guess: ask which calendar.",
    )

    @model_validator(mode="after")
    def _check_range(self) -> "CreateEventArgs":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if self.end - self.start > timedelta(hours=MAX_EVENT_HOURS):
            raise ValueError(f"an event can last at most {MAX_EVENT_HOURS} hours")
        return self


class DeleteEventArgs(BaseModel):
    """Delete one event by its id."""

    id: str = Field(min_length=1, max_length=200, description="Event id from a search.")


class ListTasksArgs(BaseModel):
    """List to-do tasks, optionally only those matching keywords or due by a day."""

    query: str | None = Field(
        None, max_length=100, description="Keywords the title or notes must contain."
    )
    due_by: date | None = Field(
        None, description="Only tasks due on or before this day (overdue included)."
    )
    include_completed: bool = False
    limit: int = Field(MAX_RESULTS, ge=1, le=MAX_RESULTS)


class CreateTaskArgs(BaseModel):
    """Add one to-do task."""

    title: str = Field(min_length=1, max_length=200)
    due: date | None = Field(None, description="Due day, YYYY-MM-DD (no time).")
    notes: str = Field("", max_length=1000)


class TaskIdArgs(BaseModel):
    """One task by its id."""

    id: str = Field(min_length=1, max_length=200, description="Task id from a list.")


class SearchDocsArgs(BaseModel):
    """Search local documents (syllabi, regulations, cinema, study plan, notes)."""

    query: str = Field(
        min_length=2,
        max_length=200,
        description="Natural language query or keywords to search in the documents.",
    )
    top_k: int = Field(
        3, ge=1, le=8, description="Number of document fragments to return."
    )

    @model_validator(mode="after")
    def _check_query(self) -> "SearchDocsArgs":
        if not any(ch.isalnum() for ch in self.query):
            raise ValueError("query must contain alphanumeric characters")
        return self


class CalendarTools:
    """One calendar file and optional document index exposed to the model as tools.

    Without a path the tools work in memory only (used by tests of read tools).
    Writes go through the _insert/_remove/_complete hooks, which a backend such
    as GoogleCalendarTools overrides; every check stays here, shared.
    """

    def __init__(
        self,
        events: list[Event],
        path: Path | None = None,
        doc_index: Any | None = None,
        tasks: list[Task] | None = None,
    ) -> None:
        self.events = events
        self.path = path
        self.doc_index = doc_index
        self.tasks = tasks if tasks is not None else []
        self.calendars: list[str] = []  # writable calendar names; empty: one file

    @classmethod
    def from_file(cls, path: Path, doc_index: Any | None = None) -> "CalendarTools":
        return cls(load_events(path), path, doc_index, tasks=load_tasks(path))

    def _save(self) -> None:
        self.events.sort(key=lambda event: event.start)
        if self.path:
            save_events(self.path, self.events, self.tasks)

    def _insert_event(self, event: Event) -> Event:
        self.events.append(event)
        self._save()
        return event

    def _remove_event(self, event: Event) -> None:
        self.events.remove(event)
        self._save()

    def _insert_task(self, task: Task) -> Task:
        self.tasks.append(task)
        self._save()
        return task

    def _complete_task(self, task: Task) -> Task:
        done = Task(task.uid, task.title, task.due, task.notes, completed=True)
        self.tasks[self.tasks.index(task)] = done
        self._save()
        return done

    def _remove_task(self, task: Task) -> None:
        self.tasks.remove(task)
        self._save()

    def _find(self, event_id: str) -> Event:
        event = next((e for e in self.events if e.uid == event_id), None)
        if event is None:
            raise ToolError("not_found", f"No event with id {event_id!r}.")
        return event

    def _find_task(self, task_id: str) -> Task:
        task = next((t for t in self.tasks if t.uid == task_id), None)
        if task is None:
            raise ToolError("not_found", f"No task with id {task_id!r}.")
        return task

    def _target_calendar(self, name: str | None) -> str:
        """The writable calendar the user named; "" when there is only the file."""
        if not self.calendars:
            return ""
        if not name:
            raise ToolError(
                "calendar_not_specified",
                "Ask the user which calendar to use; nothing was added.",
                calendars=self.calendars,
            )
        match = next((c for c in self.calendars if _fold(c) == _fold(name)), None)
        if match is None:
            match = next((c for c in self.calendars if named_in(c, name)), None)
        if match is None:
            raise ToolError(
                "calendar_not_found",
                f"No writable calendar named {name!r}; nothing was added.",
                calendars=self.calendars,
            )
        return match

    def confirmation_question(self, name: str, args: BaseModel) -> str:
        """The exact action a human must approve before a sensitive tool runs."""
        if name == "delete_event":
            event = self._find(args.id)
            return (
                f"Delete '{event.title}' ({event.start:%Y-%m-%d %H:%M}"
                f"-{event.end:%H:%M})?"
            )
        if name == "delete_task":
            return f"Delete task '{self._find_task(args.id).title}'?"
        raise ValueError(f"{name} has no confirmation question")

    def search_events(self, args: SearchEventsArgs) -> dict[str, Any]:
        words = [_fold(word) for word in args.query.split() if len(word) >= 2]
        matches = []
        for event in self.events:
            if args.start and event.end.date() < args.start:
                continue
            if args.end and event.start.date() > args.end:
                continue
            if args.calendar and _fold(event.calendar) != _fold(args.calendar):
                continue
            haystack = _fold(f"{event.title} {event.location} {event.description}")
            if all(word in haystack for word in words):
                matches.append(event)
        if args.order == "newest_first":
            matches.reverse()
        return {
            "total_matches": len(matches),
            "returned": min(len(matches), args.limit),
            "events": [event.to_dict() for event in matches[: args.limit]],
        }

    def free_busy(self, args: FreeBusyArgs) -> dict[str, Any]:
        busy = [e for e in self.events if e.start < args.end and e.end > args.start]
        free, cursor = [], args.start
        for event in busy:
            if event.all_day:
                continue  # all-day entries (birthdays) do not block time
            if event.start > cursor:
                free.append((cursor, event.start))
            cursor = max(cursor, event.end)
        if cursor < args.end:
            free.append((cursor, args.end))
        return {
            "is_free": not any(not e.all_day for e in busy),
            "busy": [e.to_dict() for e in busy],
            "free_slots": [
                {
                    "start": s.isoformat(timespec="minutes"),
                    "end": e.isoformat(timespec="minutes"),
                }
                for s, e in free
            ],
        }

    def create_event(self, args: CreateEventArgs) -> dict[str, Any]:
        calendar = self._target_calendar(args.calendar)
        title = _fold(args.title).strip()
        for event in self.events:
            same_title = _fold(event.title).strip() == title
            # Same slot catches translated titles ("Gym" vs "Siłownia").
            same_slot = event.end == args.end
            if event.start == args.start and (same_title or same_slot):
                raise ToolError(
                    "already_exists",
                    "This event is already in the calendar; nothing was added.",
                    existing=event.to_dict(),
                )
        conflicts = [
            e.to_dict()
            for e in self.events
            if not e.all_day and e.start < args.end and e.end > args.start
        ]
        event = Event(
            uid=f"agent-{uuid.uuid4().hex[:8]}@calendar.local",
            title=args.title,
            start=args.start,
            end=args.end,
            location=args.location,
            calendar=calendar,
        )
        event = self._insert_event(event)
        return {"created": event.to_dict(), "conflicts": conflicts}

    def delete_event(self, args: DeleteEventArgs) -> dict[str, Any]:
        event = self._find(args.id)
        self._remove_event(event)
        return {"deleted": event.to_dict()}

    def list_tasks(self, args: ListTasksArgs) -> dict[str, Any]:
        words = [_fold(word) for word in (args.query or "").split()]
        matches = [
            task
            for task in self.tasks
            if (args.include_completed or not task.completed)
            and (args.due_by is None or (task.due and task.due <= args.due_by))
            and all(word in _fold(f"{task.title} {task.notes}") for word in words)
        ]
        matches.sort(key=lambda t: (t.due is None, t.due or date.max, t.title))
        return {
            "total_matches": len(matches),
            "returned": min(len(matches), args.limit),
            "tasks": [task.to_dict() for task in matches[: args.limit]],
        }

    def create_task(self, args: CreateTaskArgs) -> dict[str, Any]:
        title = _fold(args.title).strip()
        for task in self.tasks:
            if not task.completed and _fold(task.title).strip() == title:
                raise ToolError(
                    "already_exists",
                    "An open task with this title exists; nothing was added.",
                    existing=task.to_dict(),
                )
        task = Task(
            uid=f"task-{uuid.uuid4().hex[:8]}@calendar.local",
            title=args.title,
            due=args.due,
            notes=args.notes,
        )
        return {"created": self._insert_task(task).to_dict()}

    def complete_task(self, args: TaskIdArgs) -> dict[str, Any]:
        return {"completed": self._complete_task(self._find_task(args.id)).to_dict()}

    def delete_task(self, args: TaskIdArgs) -> dict[str, Any]:
        task = self._find_task(args.id)
        self._remove_task(task)
        return {"deleted": task.to_dict()}

    def search_docs(self, args: SearchDocsArgs) -> dict[str, Any]:
        if self.doc_index is None:
            from agent_project.rag import DocumentIndex

            self.doc_index = DocumentIndex.load_or_build()
        return self.doc_index.search(args.query, top_k=args.top_k)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    description: str
    sensitive: bool = False  # needs a real human confirmation before it runs


TOOL_SPECS = (
    ToolSpec(
        "search_events",
        SearchEventsArgs,
        "Search past and future calendar events by keyword (no wildcards). Returns "
        "full event details including description and calendar name. Use it for "
        "questions about history or to look up a specific event. To see everything "
        "on a day, use free_busy instead.",
    ),
    ToolSpec(
        "free_busy",
        FreeBusyArgs,
        "Check a time window: returns the events that overlap it and the free "
        "gaps. Use it to check availability or find conflicts.",
    ),
    ToolSpec(
        "create_event",
        CreateEventArgs,
        "Add one event: something that happens at a set time and takes time "
        "(a meeting, a visit, an appointment, a slot the user names). Returns the "
        "created event and any overlapping events, or already_exists when an event "
        "with the same title, or any event with exactly the same start and end, is "
        "already in the calendar. calendar_not_specified or calendar_not_found "
        "lists the calendars: ask the user which one, never pick it yourself.",
    ),
    ToolSpec(
        "delete_event",
        DeleteEventArgs,
        "Delete one event by id. The application asks the user to confirm; the "
        "result is the deleted event, declined_by_user, or confirmation_required.",
        sensitive=True,
    ),
    ToolSpec(
        "list_tasks",
        ListTasksArgs,
        "List open to-do tasks (Google Tasks or the local task list), sorted by "
        "due day. For 'what do I have today' use due_by=today together with "
        "free_busy for today.",
    ),
    ToolSpec(
        "create_task",
        CreateTaskArgs,
        "Add one to-do task: something to do and check off without a set time "
        "(call someone, buy something, send something), with an optional due day. "
        "Use it when the user says task/todo/zadanie/przypomnij or gives only a "
        "day, not a time. Never create both a task and an event for one thing. "
        "Returns already_exists when an open task has the same title.",
    ),
    ToolSpec(
        "complete_task",
        TaskIdArgs,
        "Mark one task done by its id from list_tasks.",
    ),
    ToolSpec(
        "delete_task",
        TaskIdArgs,
        "Delete one task by id. The application asks the user to confirm; the "
        "result is the deleted task, declined_by_user, or confirmation_required.",
        sensitive=True,
    ),
)

SEARCH_DOCS_SPEC = ToolSpec(
    "search_docs",
    SearchDocsArgs,
    "Semantic search over local project documents (syllabi, WMI regulations, "
    "cinema repertoire, study plan, seminars, organizational notes). Returns "
    "fragments with their citable source id (e.g. 'repertuar_kina.md#chunk-2').",
)

ALL_TOOL_SPECS = (*TOOL_SPECS, SEARCH_DOCS_SPEC)


def tool_schemas(include_docs: bool = False) -> list[dict[str, Any]]:
    """Tool definitions in the OpenAI / OpenRouter function-calling format."""
    specs = ALL_TOOL_SPECS if include_docs else TOOL_SPECS
    schemas = []
    for spec in specs:
        parameters = spec.args_model.model_json_schema()
        parameters.pop("title", None)
        parameters.pop("description", None)
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": parameters,
                },
            }
        )
    return schemas


def execute_tool_call(
    tools: CalendarTools,
    name: str,
    raw_arguments: str | dict | None,
    confirm: Confirm | None = None,
    user_text: str | None = None,
) -> dict[str, Any]:
    """Check a tool request from the model and run it only if it is allowed.

    Checks, in order: the tool exists, the arguments are valid, a calendar to
    write to was named by the user (when ``user_text`` is given), and a sensitive
    tool has a real confirmation from a human (``confirm``), never from the model.
    Never raises: every problem becomes an error observation the model can read.
    """
    started = time.perf_counter()
    parsed = _parse_call(name, raw_arguments)
    if isinstance(parsed, dict):
        return parsed
    spec, args = parsed

    # The prompt says "ask which calendar"; this makes a guessed one impossible.
    chosen = getattr(args, "calendar", None)
    if (
        spec.name == "create_event"
        and tools.calendars
        and user_text is not None
        and chosen
        and not named_in(chosen, user_text)
    ):
        return _error(
            "calendar_not_specified",
            f"The user never named the calendar {chosen!r}. Ask the user which "
            "calendar to use; nothing was added.",
            calendars=tools.calendars,
        )

    try:
        if spec.sensitive:
            question = tools.confirmation_question(spec.name, args)
            if confirm is None:
                return _error(
                    "confirmation_required",
                    "This action needs the user's confirmation, which this "
                    "channel cannot collect. Nothing was changed.",
                    action=question,
                )
            if not confirm(question):
                return _error(
                    "declined_by_user",
                    "The user answered no. Nothing was changed. Do not ask again: "
                    "call finish and tell the user that nothing was deleted.",
                    action=question,
                )
        result = getattr(tools, spec.name)(args)
    except ToolError as exc:
        return _error(exc.code, exc.message, **exc.details)
    except Exception as exc:  # a tool bug must not crash the run
        return _error("tool_failed", f"{type(exc).__name__}: {exc}")
    return {
        "ok": True,
        "result": result,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def confirmation_needed(
    tools: CalendarTools, name: str, raw_arguments: str | dict | None
) -> str | None:
    """The question a human must answer before this call runs, or None.

    None also when the call is invalid: execute_tool_call reports that error.
    """
    parsed = _parse_call(name, raw_arguments)
    if isinstance(parsed, dict) or not parsed[0].sensitive:
        return None
    try:
        return tools.confirmation_question(name, parsed[1])
    except ToolError:
        return None


def _parse_call(
    name: str, raw_arguments: str | dict | None
) -> tuple[ToolSpec, BaseModel] | dict[str, Any]:
    """The tool and its validated arguments, or an error observation."""
    spec = next((s for s in ALL_TOOL_SPECS if s.name == name), None)
    if spec is None:
        allowed = ", ".join(s.name for s in ALL_TOOL_SPECS)
        return _error("unknown_tool", f"No tool named {name!r}. Allowed: {allowed}.")

    try:
        arguments = (
            raw_arguments
            if isinstance(raw_arguments, dict)
            else json.loads(raw_arguments or "{}")
        )
    except json.JSONDecodeError as exc:
        return _error("invalid_json", f"Arguments are not valid JSON: {exc.msg}.")
    if not isinstance(arguments, dict):
        return _error("invalid_json", "Arguments must be a JSON object.")

    try:
        return spec, spec.args_model.model_validate(arguments)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}"
            for err in exc.errors()
        )
        return _error("invalid_arguments", details)


def open_calendar(
    target: str | Path | None = None, doc_index: Any | None = None
) -> CalendarTools:
    """The tools for "google" (also CALENDAR_BACKEND=google) or a local file."""
    load_dotenv()  # CALENDAR_BACKEND may live in .env, also for the MCP server
    target = target or os.environ.get("CALENDAR_BACKEND") or WORKING_CALENDAR
    if str(target) == "google":
        from agent_project.google_calendar import GoogleCalendarTools

        return GoogleCalendarTools.connect(doc_index=doc_index)
    return CalendarTools.from_file(working_copy(target=Path(target)), doc_index)


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **details}}
