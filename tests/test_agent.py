import json
from datetime import date
from pathlib import Path

from agent_project import MockClient
from agent_project.agent import run_agent
from agent_project.tools import CalendarTools

CALENDAR = Path(__file__).parents[1] / "data" / "calendar.ics"
TODAY = date(2026, 10, 16)


def reply(*calls, text=None, cost=0.0001):
    return {
        "text": text,
        "tool_calls": [
            {"id": f"call_{i}", "name": name, "arguments": json.dumps(args)}
            for i, (name, args) in enumerate(calls)
        ],
        "finish_reason": "tool_calls" if calls else "stop",
        "model": "mock",
        "provider": "mock",
        "latency_ms": 5,
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost},
    }


def run(responses, tmp_path, **kwargs):
    trace = tmp_path / "trace.jsonl"
    result = run_agent(
        goal="test goal",
        client=MockClient(responses),
        tools=CalendarTools.from_file(CALENDAR),
        trace_path=trace,
        today=TODAY,
        **kwargs,
    )
    events = [json.loads(line) for line in trace.read_text("utf-8").splitlines()]
    return result, events


def test_conflict_leads_to_second_check_and_answer(tmp_path) -> None:
    result, events = run(
        [
            reply(
                ("free_busy", {"start": "2026-10-23T18:00", "end": "2026-10-23T20:00"})
            ),
            reply(
                ("free_busy", {"start": "2026-10-21T12:00", "end": "2026-10-21T13:30"})
            ),
            reply(("finish", {"outcome": "answered", "answer": "Swap to Wednesday."})),
        ],
        tmp_path,
    )
    assert result.status == "success"
    assert result.answer == "Swap to Wednesday."
    assert result.steps == 3
    tool_results = [e["payload"] for e in events if e["event"] == "tool_result"]
    assert tool_results[0]["result"]["is_free"] is False
    assert tool_results[1]["result"]["is_free"] is True


def test_trace_records_provider_tokens_cost_and_latency(tmp_path) -> None:
    _, events = run(
        [reply(("search_events", {"query": "dentysta"})), reply(text="Done.")],
        tmp_path,
    )
    assert [e["event"] for e in events] == [
        "run_start",
        "model_call",
        "tool_result",
        "model_call",
        "run_end",
    ]
    call = events[1]["payload"]
    assert call["provider"] == "mock"
    assert call["latency_ms"] == 5
    assert call["usage"]["prompt_tokens"] == 100
    end = events[-1]["payload"]
    assert end["status"] == "success"
    assert end["cost_usd"] == 0.0002
    assert end["prompt_tokens"] == 200


def test_not_found_outcome(tmp_path) -> None:
    result, _ = run(
        [
            reply(("search_events", {"query": "egzamin kryptografia"})),
            reply(("finish", {"outcome": "not_found", "answer": "No exam found."})),
        ],
        tmp_path,
    )
    assert result.status == "not_found"


def test_invalid_request_is_returned_to_model_and_run_recovers(tmp_path) -> None:
    result, events = run(
        [
            reply(("free_busy", {"start": "tomorrow"})),
            reply(
                ("free_busy", {"start": "2026-10-19T08:00", "end": "2026-10-19T12:00"})
            ),
            reply(text="You are busy 10:00-11:30."),
        ],
        tmp_path,
    )
    assert result.status == "success"
    first = next(e["payload"] for e in events if e["event"] == "tool_result")
    assert first["ok"] is False
    assert first["error"]["code"] == "invalid_arguments"


def test_repeated_rejections_stop_the_run(tmp_path) -> None:
    bad = reply(("delete_event", {"id": "evt-001"}))
    result, _ = run([bad, bad, bad, reply(text="never reached")], tmp_path)
    assert result.status == "error"
    assert result.steps == 3


def test_step_limit(tmp_path) -> None:
    search = reply(("search_events", {"query": "silownia"}))
    result, _ = run([search] * 5, tmp_path, max_steps=4)
    assert result.status == "step_limit"
    assert result.steps == 4


def test_cost_limit(tmp_path) -> None:
    expensive = reply(("search_events", {"query": "silownia"}), cost=0.06)
    result, _ = run([expensive, expensive, expensive], tmp_path)
    assert result.status == "cost_limit"
    assert result.steps == 2


def test_invalid_finish_is_rejected(tmp_path) -> None:
    result, _ = run(
        [
            reply(("finish", {"outcome": "maybe", "answer": "?"})),
            reply(("finish", {"outcome": "answered", "answer": "OK"})),
        ],
        tmp_path,
    )
    assert result.status == "success"
    assert result.steps == 2


def test_model_failure_ends_run_with_error(tmp_path) -> None:
    result, events = run([], tmp_path)  # empty mock queue raises
    assert result.status == "error"
    assert events[-1]["payload"]["status"] == "error"
