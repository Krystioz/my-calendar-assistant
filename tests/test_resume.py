"""A clarifying question survives the process: SQLite checkpoint, then resume."""

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from agent_project import MockClient
from agent_project.agent import ConversationError, run_agent
from agent_project.tools import CalendarTools, working_copy

CALENDAR = Path(__file__).parents[1] / "data" / "calendar.ics"
TODAY = date(2026, 10, 16)
LAB_ID = "evt-018@calendar.local"
BUSY = ("free_busy", {"start": "2026-10-23T18:00", "end": "2026-10-23T20:45"})


class RecordingClient(MockClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append(copy.deepcopy(messages))
        return super().complete(messages, tools)


def reply(*calls):
    return {
        "tool_calls": [
            {"id": f"call_{name}_{i}", "name": name, "arguments": json.dumps(args)}
            for i, (name, args) in enumerate(calls)
        ],
        "usage": {"cost": 0.0001},
    }


def clarify(question):
    return reply(("finish", {"outcome": "clarify", "answer": question}))


def answered(text, citations=()):
    return reply(
        ("finish", {"outcome": "answered", "answer": text, "citations": citations})
    )


def run_turn(tmp_path, goal, responses, thread=None, max_clarifications=3):
    """One turn as a CLI run does it: fresh SQLite connection, graph and client."""
    client = RecordingClient(responses)
    calendar = working_copy(CALENDAR, tmp_path / "calendar.ics")
    with SqliteSaver.from_conn_string(str(tmp_path / "c.sqlite")) as saver:
        result = run_agent(
            goal=goal,
            client=client,
            tools=CalendarTools.from_file(calendar),
            trace_path=tmp_path / f"run-{len(list(tmp_path.glob('run-*')))}.jsonl",
            today=TODAY,
            max_steps=12,
            max_cost_usd=0.10,
            max_clarifications=max_clarifications,
            checkpointer=saver,
            resume_thread=thread,
        )
    return result, client


def assert_tool_calls_paired(messages):
    answered_ids = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
    asked_ids = [
        call["id"]
        for m in messages
        if m["role"] == "assistant"
        for call in m.get("tool_calls", [])
    ]
    assert asked_ids == answered_ids


def test_answer_in_a_new_process_continues_the_same_conversation(tmp_path) -> None:
    first, _ = run_turn(
        tmp_path,
        "Umów spotkanie z Tomkiem",
        [reply(BUSY), clarify("Kiedy i na jak długo?")],
    )
    assert first.status == "clarify"
    assert first.answer == "Kiedy i na jak długo?"

    second, client = run_turn(
        tmp_path,
        "12 grudnia o 13, godzina",
        [answered("Umówione.", [LAB_ID])],
        thread=first.thread_id,
    )
    assert second.status == "success"
    assert second.thread_id == first.thread_id
    # LAB_ID came from the first process; citations still verify against it.
    assert second.citations == [LAB_ID]
    assert second.steps == 1  # the step budget is per turn

    history = client.requests[0]
    assert history[1] == {"role": "user", "content": "Umów spotkanie z Tomkiem"}
    assert any(m["role"] == "tool" and LAB_ID in m["content"] for m in history)
    finish_call = history[-3]["tool_calls"][0]
    assert finish_call["function"]["name"] == "finish"
    assert history[-2] == {
        "role": "tool",
        "tool_call_id": finish_call["id"],
        "content": json.dumps({"ok": True, "result": {"delivered": True}}),
    }
    assert history[-1] == {"role": "user", "content": "12 grudnia o 13, godzina"}
    assert_tool_calls_paired(history)

    start = json.loads(second.trace_path.read_text("utf-8").splitlines()[0])
    assert start["payload"]["thread_id"] == first.thread_id
    assert start["payload"]["resumed"] is True


def test_several_clarifications_can_each_be_resumed(tmp_path) -> None:
    first, _ = run_turn(tmp_path, "Umów spotkanie", [clarify("Z kim?")])
    second, _ = run_turn(
        tmp_path, "Z Tomkiem", [clarify("Kiedy?")], thread=first.thread_id
    )
    assert (second.status, second.answer) == ("clarify", "Kiedy?")

    third, client = run_turn(
        tmp_path, "Jutro o 13", [answered("Umówione.")], thread=first.thread_id
    )
    assert third.status == "success"
    users = [m["content"] for m in client.requests[0] if m["role"] == "user"]
    assert users == ["Umów spotkanie", "Z Tomkiem", "Jutro o 13"]
    assert_tool_calls_paired(client.requests[0])


def test_clarification_limit_stops_the_conversation(tmp_path) -> None:
    first, _ = run_turn(
        tmp_path, "Umów spotkanie", [clarify("Z kim?")], max_clarifications=1
    )
    second, _ = run_turn(
        tmp_path,
        "Z Tomkiem",
        [clarify("Kiedy?")],
        thread=first.thread_id,
        max_clarifications=1,
    )
    assert second.status == "step_limit"
    assert "1 clarifying" in second.answer


def test_only_a_waiting_conversation_takes_an_answer(tmp_path) -> None:
    with pytest.raises(ConversationError, match="No saved conversation"):
        run_turn(tmp_path, "Jutro", [], thread="unknown")

    done, _ = run_turn(tmp_path, "Co mam jutro?", [answered("Nic.")])
    with pytest.raises(ConversationError, match="has ended"):
        run_turn(tmp_path, "Jutro", [], thread=done.thread_id)


def test_clarification_batched_with_a_tool_call_is_resumable(tmp_path) -> None:
    finish = ("finish", {"outcome": "clarify", "answer": "Kiedy?"})
    first, _ = run_turn(tmp_path, "Umów spotkanie", [reply(BUSY, finish)])
    assert (first.status, first.answer) == ("clarify", "Kiedy?")

    second, client = run_turn(
        tmp_path,
        "Jutro o 13",
        [answered("Umówione.", [LAB_ID])],
        thread=first.thread_id,
    )
    assert second.status == "success"
    assert_tool_calls_paired(client.requests[0])


def test_batched_clarification_respects_the_limit(tmp_path) -> None:
    finish = ("finish", {"outcome": "clarify", "answer": "Kiedy?"})
    result, _ = run_turn(
        tmp_path, "Umów spotkanie", [reply(BUSY, finish)], max_clarifications=0
    )
    assert result.status == "step_limit"
