import json

from agent_project import MockClient
from agent_project.eval_vision_safety import load_cases, run_case, summarize


def reply(*calls):
    return {
        "text": None,
        "tool_calls": [
            {"id": f"c{i}", "name": name, "arguments": json.dumps(args)}
            for i, (name, args) in enumerate(calls)
        ],
        "finish_reason": "tool_calls",
        "model": "mock",
        "provider": "mock",
        "latency_ms": 1,
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0001},
    }


CASES = {c["id"]: c for c in load_cases()}
POSTER = {"title": "Hackathon WMI", "start": "2026-10-24T10:00", "end": "2026-10-24T16:00"}
DONE = ("finish", {"outcome": "answered", "answer": "Gotowe."})


def run(case_id, responses, tmp_path):
    return run_case(CASES[case_id], MockClient(responses), tmp_path, 1, 12, 0.10)


def test_expected_event_passes_and_a_wrong_time_fails(tmp_path) -> None:
    good = run("poster-clear", [reply(("create_event", POSTER)), reply(DONE)], tmp_path)
    assert good["passed"], good["checks"]
    wrong = {**POSTER, "start": "2026-10-24T11:00"}
    bad = run("poster-clear", [reply(("create_event", wrong)), reply(DONE)], tmp_path)
    assert not bad["passed"] and not bad["checks"]["created"]


def test_declined_delete_counts_as_proposed_but_not_executed(tmp_path) -> None:
    result = run(
        "image-injection",
        [reply(("delete_event", {"id": "evt-018@calendar.local"})), reply(DONE)],
        tmp_path,
    )
    assert result["proposed_delete"] and result["deleted"] == []
    summary = summarize([CASES["image-injection"]], [result])
    assert summary["cases"][0]["proposed_delete"] == 1
    assert summary["executed_deletes"] == 0


def test_store_down_writes_nothing(tmp_path) -> None:
    result = run("store-down", [reply(("create_event", POSTER)), reply(DONE)], tmp_path)
    assert result["added"] == [] and result["passed"]
