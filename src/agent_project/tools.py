"""Calendar tools and the code-side checks for every tool request.

The agent works on a local working copy of the calendar, so the source file in
data/ never changes. All times are local wall-clock times (no time zones).
"""

import json
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.uid,
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


def working_copy(
    source: Path = SOURCE_CALENDAR, target: Path = WORKING_CALENDAR
) -> Path:
    """Return the working calendar, copying the source the first time."""
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return target


def save_events(path: Path, events: list[Event]) -> None:
    calendar = icalendar.Calendar()
    calendar.add("prodid", "-//my-calendar-assistant//agent working copy//EN")
    calendar.add("version", "2.0")
    for event in events:
        calendar.add_component(event.to_ical())
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


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so 'Wykład' matches 'wyklad'."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


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


class CalendarTools:
    """One calendar file exposed to the model as tools.

    Without a path the tools work in memory only (used by tests of read tools).
    """

    def __init__(self, events: list[Event], path: Path | None = None) -> None:
        self.events = events
        self.path = path

    @classmethod
    def from_file(cls, path: Path) -> "CalendarTools":
        return cls(load_events(path), path)

    def _save(self) -> None:
        self.events.sort(key=lambda event: event.start)
        if self.path:
            save_events(self.path, self.events)

    def _find(self, event_id: str) -> Event:
        event = next((e for e in self.events if e.uid == event_id), None)
        if event is None:
            raise ToolError("not_found", f"No event with id {event_id!r}.")
        return event

    def confirmation_question(self, name: str, args: BaseModel) -> str:
        """The exact action a human must approve before a sensitive tool runs."""
        if name == "delete_event":
            event = self._find(args.id)
            return (
                f"Delete '{event.title}' ({event.start:%Y-%m-%d %H:%M}"
                f"-{event.end:%H:%M})?"
            )
        raise ValueError(f"{name} has no confirmation question")

    def search_events(self, args: SearchEventsArgs) -> dict[str, Any]:
        words = [_fold(word) for word in args.query.split() if len(word) >= 2]
        matches = []
        for event in self.events:
            if args.start and event.end.date() < args.start:
                continue
            if args.end and event.start.date() > args.end:
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
        )
        self.events.append(event)
        self._save()
        return {"created": event.to_dict(), "conflicts": conflicts}

    def delete_event(self, args: DeleteEventArgs) -> dict[str, Any]:
        event = self._find(args.id)
        self.events.remove(event)
        self._save()
        return {"deleted": event.to_dict()}


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
        "full event details including description. Use it for questions about "
        "history or to look up a specific event. To see everything on a day, use "
        "free_busy instead.",
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
        "Add one event. Returns the created event and any overlapping events, or "
        "already_exists when an event with the same title, or any event with "
        "exactly the same start and end, is already in the calendar.",
    ),
    ToolSpec(
        "delete_event",
        DeleteEventArgs,
        "Delete one event by id. The application asks the user to confirm; the "
        "result is the deleted event, declined_by_user, or confirmation_required.",
        sensitive=True,
    ),
)


def tool_schemas() -> list[dict[str, Any]]:
    """Tool definitions in the OpenAI / OpenRouter function-calling format."""
    schemas = []
    for spec in TOOL_SPECS:
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
) -> dict[str, Any]:
    """Check a tool request from the model and run it only if it is allowed.

    Checks, in order: the tool exists, the arguments are valid, and a sensitive
    tool has a real confirmation from a human (``confirm``), never from the model.
    Never raises: every problem becomes an error observation the model can read.
    """
    started = time.perf_counter()
    spec = next((s for s in TOOL_SPECS if s.name == name), None)
    if spec is None:
        allowed = ", ".join(s.name for s in TOOL_SPECS)
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
        args = spec.args_model.model_validate(arguments)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}"
            for err in exc.errors()
        )
        return _error("invalid_arguments", details)

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


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **details}}
