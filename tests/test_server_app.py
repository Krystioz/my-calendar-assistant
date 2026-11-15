from datetime import date, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from agent_project import server_app, studio
from agent_project.tools import CalendarTools, Event, Task, save_events

UI = "http://localhost:5173"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    path = tmp_path / "c.ics"
    save_events(
        path,
        [
            Event(
                "in@x", "Inside", datetime(2026, 10, 16, 9), datetime(2026, 10, 16, 10)
            ),
            Event(
                "edge@x",
                "Ends at start",
                datetime(2026, 10, 14, 23),
                datetime(2026, 10, 15),
            ),
            Event(
                "out@x",
                "Outside",
                datetime(2026, 10, 20, 9),
                datetime(2026, 10, 20, 10),
            ),
        ],
        [Task("t@x", "Done", due=date(2026, 1, 1), completed=True)],
    )
    opened = []
    monkeypatch.setattr(studio, "CALENDARS", {"local": path})

    def fake_open(target):
        opened.append(target)
        return CalendarTools.from_file(target)

    monkeypatch.setattr(studio, "open_calendar", fake_open)
    test_client = TestClient(server_app.app)
    test_client.opened = opened
    return test_client


def test_calendar_returns_events_overlapping_the_range_and_all_tasks(client) -> None:
    response = client.get(
        "/calendar",
        params={"calendar": "local", "start": "2026-10-15", "end": "2026-10-16"},
    )
    assert response.status_code == 200
    body = response.json()
    assert [e["id"] for e in body["events"]] == ["in@x"]
    assert body["tasks"][0]["completed"] is True
    assert body["calendars"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"calendar": "C:/Windows/x.ics", "start": "2026-10-15", "end": "2026-10-16"},
        {"calendar": "local", "start": "15.10.2026", "end": "2026-10-16"},
        {"calendar": "local", "start": "2026-10-16", "end": "2026-10-15"},
        {"calendar": "local", "start": "2026-01-01", "end": "2026-12-31"},
    ],
)
def test_calendar_rejects_unknown_names_and_bad_ranges(client, params) -> None:
    assert client.get("/calendar", params=params).status_code == 400
    assert client.opened == []


def test_docs_returns_one_chunk_or_404(monkeypatch) -> None:
    chunk = SimpleNamespace(id="a.md#chunk-1", source="a.md", heading="H", text="T")
    monkeypatch.setattr(
        server_app, "_doc_index", lambda: SimpleNamespace(chunks=[chunk])
    )
    client = TestClient(server_app.app)
    assert client.get("/docs", params={"id": "a.md#chunk-1"}).json() == {
        "id": "a.md#chunk-1",
        "source": "a.md",
        "heading": "H",
        "text": "T",
    }
    assert client.get("/docs", params={"id": "missing.md#chunk-9"}).status_code == 404
    # langgraph-api's CORS middleware sits inside this route's middleware.
    cors = client.get(
        "/docs", params={"id": "a.md#chunk-1"}, headers={"Origin": UI}
    ).headers
    assert cors["access-control-allow-origin"] == UI


def test_limits_come_from_the_settings() -> None:
    body = TestClient(server_app.app).get("/limits").json()
    assert set(body) == {"max_steps", "max_cost_usd", "max_clarifications"}
