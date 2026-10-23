"""Read-only calendar tools and the code-side checks for every tool request.

All times are local wall-clock times (the sample calendar has no time zones).
"""

import json
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import icalendar
from pydantic import BaseModel, Field, ValidationError, model_validator

MAX_RESULTS = 20
MAX_FREE_BUSY_DAYS = 14


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
        return self


class FreeBusyArgs(BaseModel):
    """List busy events and free gaps between start and end."""

    start: datetime = Field(description="Window start, YYYY-MM-DDTHH:MM.")
    end: datetime = Field(description="Window end, YYYY-MM-DDTHH:MM.")

    @model_validator(mode="after")
    def _check_range(self) -> "FreeBusyArgs":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if self.end - self.start > timedelta(days=MAX_FREE_BUSY_DAYS):
            raise ValueError(f"window must be at most {MAX_FREE_BUSY_DAYS} days")
        return self


class CalendarTools:
    """Read-only view of one calendar file, exposed to the model as tools."""

    def __init__(self, events: list[Event]) -> None:
        self.events = events

    @classmethod
    def from_file(cls, path: Path) -> "CalendarTools":
        return cls(load_events(path))

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


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    description: str


TOOL_SPECS = (
    ToolSpec(
        "search_events",
        SearchEventsArgs,
        "Search past and future calendar events by keyword. Returns full event "
        "details including description. Use it for questions about history or "
        "to look up a specific event.",
    ),
    ToolSpec(
        "free_busy",
        FreeBusyArgs,
        "Check a time window: returns the events that overlap it and the free "
        "gaps. Use it to check availability or find conflicts.",
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
    tools: CalendarTools, name: str, raw_arguments: str | dict | None
) -> dict[str, Any]:
    """Check a tool request from the model and run it only if it is valid.

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
        result = getattr(tools, spec.name)(args)
    except Exception as exc:  # a tool bug must not crash the run
        return _error("tool_failed", f"{type(exc).__name__}: {exc}")
    return {
        "ok": True,
        "result": result,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}
