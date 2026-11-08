"""Tests for local RAG retrieval, code-side citation checks, and LangGraph decisions."""

import json
from datetime import date
from pathlib import Path

import pytest

from agent_project import MockClient
from agent_project.agent import run_agent, verify_citations_in_code
from agent_project.rag import DOCS_DIR, INDEX_PATH, DocumentIndex, split_markdown
from agent_project.tools import CalendarTools, working_copy

CALENDAR = Path(__file__).parents[1] / "data" / "calendar.ics"
TODAY = date(2026, 10, 16)


@pytest.fixture(scope="module")
def doc_index() -> DocumentIndex:
    return DocumentIndex.load_or_build(DOCS_DIR, INDEX_PATH)


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


def run(responses, tmp_path, doc_index, **kwargs):
    trace = tmp_path / "trace.jsonl"
    calendar = working_copy(CALENDAR, tmp_path / "calendar.ics")
    tools = CalendarTools.from_file(calendar, doc_index=doc_index)
    result = run_agent(
        goal="test rag goal",
        client=MockClient(responses),
        tools=tools,
        trace_path=trace,
        today=TODAY,
        **kwargs,
    )
    events = [json.loads(line) for line in trace.read_text("utf-8").splitlines()]
    return result, events


def test_split_markdown_preserves_headings_and_title() -> None:
    md = "# Tytuł Dokumentu\n\n## Sekcja A\nTreść A.\n\n## Sekcja B\nTreść B."
    parts = split_markdown("plik.md", md)
    assert len(parts) == 2
    assert parts[0][0] == "Sekcja A"
    assert "Tytuł Dokumentu — Sekcja A" in parts[0][1]
    assert "Treść A." in parts[0][1]


def test_index_keeps_source_name_for_every_fragment(doc_index: DocumentIndex) -> None:
    assert len(doc_index.chunks) >= 15
    for chunk in doc_index.chunks:
        assert chunk.source.endswith(".md")
        assert chunk.id.startswith(f"{chunk.source}#chunk-")
        assert len(chunk.embedding) == 384


def test_retrieval_finds_relevant_fragments_across_topics(
    doc_index: DocumentIndex,
) -> None:
    cinema = doc_index.search("Diuna pokaz w kinie październik", top_k=2)
    assert cinema["fragments"][0]["source"] == "repertuar_kina.md"
    assert any("Diuna" in f["text"] for f in cinema["fragments"])

    crypto = doc_index.search("kartka A4 notatki egzamin kryptografia", top_k=2)
    assert crypto["fragments"][0]["source"] == "sylabus_kryptografia.md"

    seminars = doc_index.search("Koło Naukowe Golem AI hackathon", top_k=3)
    assert any(
        f["source"] == "plan_studiow_i_seminaria.md" for f in seminars["fragments"]
    )


def test_verify_citations_requires_at_least_one_and_all_valid() -> None:
    retrieved = {"repertuar_kina.md#chunk-2", "evt-018@calendar.local"}

    # Missing citations when required -> rejected
    ok, _, err = verify_citations_in_code(
        "Seans o 18:00.", [], retrieved, require_citations=True
    )
    assert not ok
    assert err["error"]["code"] == "missing_citations"

    # One real + one fake citation -> rejected (all citations must be real)
    ok, _, err = verify_citations_in_code(
        "Seans o 18:00.",
        ["repertuar_kina.md#chunk-2", "zmyslony.md#chunk-9"],
        retrieved,
        require_citations=True,
    )
    assert not ok
    assert err["error"]["code"] == "unverified_citations"

    # Unverified inline bracket citation in text -> rejected
    ok, _, err = verify_citations_in_code(
        "Seans o 18:00 [repertuar_kina.md#chunk-99].",
        ["repertuar_kina.md#chunk-2"],
        retrieved,
        require_citations=True,
    )
    assert not ok
    assert err["error"]["code"] == "unverified_citations"

    # All citations valid and non-empty -> accepted
    ok, cited, err = verify_citations_in_code(
        "Seans o 18:00 [repertuar_kina.md#chunk-2].",
        ["repertuar_kina.md#chunk-2", "evt-018@calendar.local"],
        retrieved,
        require_citations=True,
    )
    assert ok
    assert err is None
    assert cited == ["repertuar_kina.md#chunk-2", "evt-018@calendar.local"]


def test_first_retrieval_insufficient_leads_to_second_search_and_verified_answer(
    tmp_path, doc_index: DocumentIndex
) -> None:
    # Step 1: vague query does not give calendar conflict info
    # Step 2: refined query for OS lab make-up rules + calendar free_busy check
    # Step 3: finish with verified citations from both sources
    result, events = run(
        [
            reply(("search_docs", {"query": "Diuna październik kino", "top_k": 2})),
            reply(
                (
                    "search_docs",
                    {
                        "query": "odrabianie nieobecności laboratorium grupa 1",
                        "top_k": 2,
                    },
                ),
                ("free_busy", {"start": "2026-10-23T17:30", "end": "2026-10-23T21:00"}),
            ),
            reply(
                (
                    "finish",
                    {
                        "outcome": "answered",
                        "answer": (
                            "W piątek 23.10 o 18:00 jest Diuna "
                            "[repertuar_kina.md#chunk-2], ale koliduje z labami SO "
                            "[evt-018@calendar.local], które odrobisz w środę "
                            "[sylabus_systemy_operacyjne.md#chunk-2]."
                        ),
                        "citations": [
                            "repertuar_kina.md#chunk-2",
                            "evt-018@calendar.local",
                            "sylabus_systemy_operacyjne.md#chunk-2",
                        ],
                    },
                )
            ),
        ],
        tmp_path,
        doc_index,
    )
    assert result.status == "success"
    assert result.steps == 3
    assert "repertuar_kina.md#chunk-2" in result.citations
    assert "sylabus_systemy_operacyjne.md#chunk-2" in result.citations
    assert "evt-018@calendar.local" in result.citations


def test_unverified_or_empty_citations_are_rejected_and_recovered(
    tmp_path, doc_index: DocumentIndex
) -> None:
    result, events = run(
        [
            reply(
                (
                    "search_docs",
                    {"query": "próg zaliczenia systemy operacyjne", "top_k": 2},
                )
            ),
            # Attempt 1: empty citations after searching docs -> rejected
            reply(
                (
                    "finish",
                    {
                        "outcome": "answered",
                        "answer": "Próg to 55 pkt.",
                        "citations": [],
                    },
                )
            ),
            # Attempt 2: one real + one hallucinated citation -> rejected
            reply(
                (
                    "finish",
                    {
                        "outcome": "answered",
                        "answer": "Próg to 55 pkt.",
                        "citations": [
                            "sylabus_systemy_operacyjne.md#chunk-3",
                            "sylabus_systemy_operacyjne.md#chunk-99",
                        ],
                    },
                )
            ),
            # Attempt 3: only verified citation -> accepted
            reply(
                (
                    "finish",
                    {
                        "outcome": "answered",
                        "answer": (
                            "Próg zaliczenia wynosi 55 pkt "
                            "[sylabus_systemy_operacyjne.md#chunk-3]."
                        ),
                        "citations": ["sylabus_systemy_operacyjne.md#chunk-3"],
                    },
                )
            ),
        ],
        tmp_path,
        doc_index,
    )
    assert result.status == "success"
    assert result.steps == 4
    tool_results = [e["payload"] for e in events if e["event"] == "tool_result"]
    assert tool_results[1]["error"]["code"] == "missing_citations"
    assert tool_results[2]["error"]["code"] == "unverified_citations"


def test_ambiguous_intent_asks_for_clarification(
    tmp_path, doc_index: DocumentIndex
) -> None:
    result, _ = run(
        [
            reply(("search_docs", {"query": "seanse środa październik", "top_k": 2})),
            reply(
                (
                    "finish",
                    {
                        "outcome": "clarify",
                        "answer": (
                            "W środę 21.10 są dwa seanse 'Łowcy androidów 2049' "
                            "(16:00 oraz 21:15). O którą godzinę pytasz?"
                        ),
                    },
                )
            ),
        ],
        tmp_path,
        doc_index,
    )
    assert result.status == "clarify"
    assert "16:00" in result.answer


def test_missing_evidence_abstains_and_names_what_is_missing(
    tmp_path, doc_index: DocumentIndex
) -> None:
    result, _ = run(
        [
            reply(
                (
                    "search_docs",
                    {"query": "cennik popcorn napoje bar kinowy", "top_k": 2},
                )
            ),
            reply(
                (
                    "finish",
                    {
                        "outcome": "not_found",
                        "answer": (
                            "Brak informacji: w dokumentach znajduje się repertuar "
                            "i ceny biletów Kina Muza, ale cennik baru kinowego "
                            "(popcorn, napoje) nie jest opublikowany."
                        ),
                    },
                )
            ),
        ],
        tmp_path,
        doc_index,
    )
    assert result.status == "not_found"
    assert "cennik" in result.answer.lower()


def test_eval_questions_and_retrieval_metrics(doc_index: DocumentIndex) -> None:
    from agent_project.eval_rag import evaluate_retrieval, load_eval_questions

    questions = load_eval_questions()
    assert len(questions) >= 10
    unanswerable = [q for q in questions if q["expected_outcome"] == "not_found"]
    assert len(unanswerable) >= 3

    metrics = evaluate_retrieval(index=doc_index, questions=questions, top_k=3)
    assert metrics["evaluated_queries"] >= 7
    assert metrics["hit_at_k"] >= 0.85
