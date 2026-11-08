"""Separate evaluation of retrieval quality (Hit@1, Hit@3) and agent answer quality."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from agent_project.agent import run_agent
from agent_project.llm import ModelClient
from agent_project.rag import DOCS_DIR, INDEX_PATH, DocumentIndex
from agent_project.tools import SOURCE_CALENDAR, CalendarTools, working_copy

EVAL_QUESTIONS_PATH = Path("data/eval_questions.json")
DEFAULT_TODAY = date(2026, 10, 16)


def load_eval_questions(path: Path = EVAL_QUESTIONS_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_retrieval(
    index: DocumentIndex | None = None,
    questions: list[dict[str, Any]] | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Measure retrieval quality (Hit@1 and Hit@k) separately from answer generation."""
    idx = index or DocumentIndex.load_or_build(DOCS_DIR, INDEX_PATH)
    items = questions or load_eval_questions()

    doc_cases = []
    hits_at_1 = 0
    hits_at_k = 0

    for item in items:
        expected_docs = [
            s
            for s in item["expected_sources"]
            if s.endswith(tuple(f"#chunk-{i}" for i in range(1, 20)))
        ]
        if not expected_docs:
            continue
        res = idx.search(item["retrieval_query"], top_k=top_k)
        returned_ids = [f["id"] for f in res["fragments"]]
        hit_1 = bool(returned_ids and returned_ids[0] in expected_docs)
        hit_k = any(exp in returned_ids for exp in expected_docs)
        hits_at_1 += int(hit_1)
        hits_at_k += int(hit_k)
        doc_cases.append(
            {
                "id": item["id"],
                "query": item["retrieval_query"],
                "expected_docs": expected_docs,
                "returned_ids": returned_ids,
                "hit_at_1": hit_1,
                "hit_at_k": hit_k,
            }
        )

    total = len(doc_cases) or 1
    return {
        "evaluated_queries": len(doc_cases),
        "hit_at_1": round(hits_at_1 / total, 3),
        "hit_at_k": round(hits_at_k / total, 3),
        "top_k": top_k,
        "cases": doc_cases,
    }


def evaluate_agent_set(
    client: ModelClient,
    trace_dir: Path = Path("traces/eval"),
    questions: list[dict[str, Any]] | None = None,
    today: date = DEFAULT_TODAY,
) -> dict[str, Any]:
    """Run the full agent workflow on eval questions and score outcomes + citations."""
    idx = DocumentIndex.load_or_build(DOCS_DIR, INDEX_PATH)
    items = questions or load_eval_questions()
    trace_dir.mkdir(parents=True, exist_ok=True)

    results = []
    outcome_matches = 0
    citation_matches = 0

    for item in items:
        cal_path = working_copy(SOURCE_CALENDAR, trace_dir / f"{item['id']}.ics")
        tools = CalendarTools.from_file(cal_path, doc_index=idx)
        trace_path = trace_dir / f"{item['id']}.jsonl"
        run_res = run_agent(
            goal=item["question"],
            client=client,
            tools=tools,
            trace_path=trace_path,
            today=today,
            require_citations=(item["expected_outcome"] == "answered"),
        )
        expected_status = (
            "success"
            if item["expected_outcome"] == "answered"
            else item["expected_outcome"]
        )
        status_ok = run_res.status == expected_status
        expected_sources = set(item["expected_sources"])
        cited_ok = (
            expected_sources.issubset(set(run_res.citations))
            if item["expected_outcome"] == "answered"
            else True
        )
        outcome_matches += int(status_ok)
        citation_matches += int(cited_ok)
        results.append(
            {
                "id": item["id"],
                "expected_status": expected_status,
                "actual_status": run_res.status,
                "status_ok": status_ok,
                "expected_sources": list(expected_sources),
                "actual_citations": run_res.citations,
                "citations_ok": cited_ok,
                "steps": run_res.steps,
                "cost_usd": round(run_res.cost_usd, 6),
                "answer": run_res.answer,
                "trace": trace_path.as_posix(),
            }
        )

    total = len(results) or 1
    return {
        "total_questions": len(results),
        "outcome_accuracy": round(outcome_matches / total, 3),
        "citation_accuracy": round(citation_matches / total, 3),
        "results": results,
    }
