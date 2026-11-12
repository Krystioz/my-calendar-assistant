from types import SimpleNamespace

from agent_project import eval_rag


def test_fact_scoring_is_separate_from_success_and_citations():
    rubric = [{"fact": "fee", "pattern": r"90\s*zł"}]
    assert eval_rag.score_answer("Wizyta kosztuje 90 zł", rubric)["fact_coverage"] == 1
    score = eval_rag.score_answer("Nie wiem", rubric)
    assert score["fact_coverage"] == 0
    assert score["manual_review_required"] is True


def test_retrieval_accepts_large_chunk_numbers():
    index = SimpleNamespace(
        search=lambda *a, **kw: {"fragments": [{"id": "doc.md#chunk-120"}]}
    )
    result = eval_rag.evaluate_retrieval(
        index=index,
        questions=[
            {
                "id": "q",
                "expected_sources": ["doc.md#chunk-120"],
                "retrieval_query": "query",
            }
        ],
    )
    assert result["hit_at_1"] == 1


def test_agent_execution_has_no_expected_label_flags_and_fresh_state(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(eval_rag.DocumentIndex, "load_or_build", lambda *a: object())
    monkeypatch.setattr(eval_rag.CalendarTools, "from_file", lambda *a, **kw: object())
    calendars = []
    monkeypatch.setattr(
        eval_rag,
        "working_copy",
        lambda source, target: calendars.append(target) or target,
    )
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status="success",
            citations=["doc.md#chunk-1"],
            steps=1,
            cost_usd=0.02,
            answer="Wrong answer",
        )

    monkeypatch.setattr(eval_rag, "run_agent", run)
    questions = [
        {
            "id": "q",
            "question": "Question",
            "expected_outcome": "answered",
            "expected_sources": ["doc.md#chunk-1"],
            "answer_rubric": [{"fact": "truth", "pattern": "correct fact"}],
        }
    ]
    first = eval_rag.evaluate_agent_set(
        object(), tmp_path, questions, max_total_cost_usd=0.03
    )
    second = eval_rag.evaluate_agent_set(
        object(), tmp_path, questions, max_total_cost_usd=0.01
    )
    assert first["outcome_accuracy"] == first["citation_accuracy"] == 1
    assert first["fact_coverage"] == 0
    assert second["fact_coverage"] == 0
    assert calendars[0] != calendars[1]
    assert calls[0]["trace_path"] != calls[1]["trace_path"]
    assert "require_citations" not in calls[0]
    assert calls[1]["max_cost_usd"] == 0.01


def test_budget_stops_additional_questions(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_rag.DocumentIndex, "load_or_build", lambda *a: object())
    monkeypatch.setattr(eval_rag.CalendarTools, "from_file", lambda *a, **kw: object())
    monkeypatch.setattr(eval_rag, "working_copy", lambda source, target: target)
    monkeypatch.setattr(
        eval_rag,
        "run_agent",
        lambda **kw: SimpleNamespace(
            status="not_found", citations=[], steps=1, cost_usd=0.04, answer="brak"
        ),
    )
    q = {
        "id": "q",
        "question": "q",
        "expected_outcome": "not_found",
        "expected_sources": [],
        "answer_rubric": [{"fact": "missing", "pattern": "brak"}],
    }
    result = eval_rag.evaluate_agent_set(
        object(), tmp_path, [q, dict(q, id="q2")], max_total_cost_usd=0.03
    )
    assert result["total_questions"] == 1
    assert result["complete"] is False
    assert result["citation_accuracy"] is None


def test_all_questions_have_compilable_rubrics():
    import re

    for question in eval_rag.load_eval_questions():
        assert question["answer_rubric"]
        for fact in question["answer_rubric"]:
            re.compile(fact["pattern"])


def test_routing_counts_only_successful_tool_calls(tmp_path):
    import json

    trace = tmp_path / "t.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps({"event": "tool_result", "payload": {"name": n, "ok": ok}})
            for n, ok in [("create_event", False), ("create_task", True)]
        ),
        encoding="utf-8",
    )
    task = {"expected_tools": ["create_task"], "forbidden_tools": ["create_event"]}
    assert eval_rag.score_routing(trace, task) is True
    assert eval_rag.score_routing(trace, {"expected_tools": ["list_tasks"]}) is False
    assert eval_rag.score_routing(trace, {}) is None
