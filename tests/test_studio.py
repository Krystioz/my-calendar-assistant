import asyncio
import json
from pathlib import Path

from agent_project import MockClient, studio
from agent_project.tools import CalendarTools, working_copy

CALENDAR = Path(__file__).parents[1] / "data" / "calendar.ics"


def test_langgraph_json_points_at_the_factory() -> None:
    config = json.loads(Path("langgraph.json").read_text("utf-8"))
    assert config["graphs"]["calendar_agent"].endswith("studio.py:make_graph")


def test_studio_graph_takes_only_a_goal_and_leaves_storage_to_the_server(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(studio, "_client", lambda *_: MockClient([]))
    monkeypatch.setattr(
        studio,
        "open_calendar",
        lambda: CalendarTools.from_file(working_copy(CALENDAR, tmp_path / "c.ics")),
    )
    graph = asyncio.run(studio.make_graph({}))
    assert graph.get_input_jsonschema()["required"] == ["goal"]
    assert list(graph.get_input_jsonschema()["properties"]) == ["goal"]
    assert graph.checkpointer is None
    assert "start" in graph.get_graph().nodes


def test_empty_goal_is_a_clear_error(tmp_path, monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(studio, "_client", lambda *_: MockClient([]))
    monkeypatch.setattr(
        studio,
        "open_calendar",
        lambda: CalendarTools.from_file(working_copy(CALENDAR, tmp_path / "c.ics")),
    )
    graph = asyncio.run(studio.make_graph({}))
    with pytest.raises(ValueError, match="non-empty 'goal'"):
        graph.invoke({"goal": " "})
