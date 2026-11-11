"""Separate evaluation of retrieval quality (Hit@1, Hit@3) and agent answer quality."""

import argparse
import json
import re
import tempfile
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from agent_project.agent import run_agent
from agent_project.llm import ModelClient
from agent_project.rag import DOCS_DIR, INDEX_PATH, DocumentIndex
from agent_project.tools import SOURCE_CALENDAR, CalendarTools, working_copy

EVAL_QUESTIONS_PATH = Path("data/eval_questions.json")
DEFAULT_TODAY = date(2026, 10, 16)
# Fixed here, not read from calendar-agent.toml, so results stay comparable.
EVAL_MAX_STEPS = 12
EVAL_MAX_COST_PER_QUESTION_USD = 0.10
EVAL_MAX_CLARIFICATIONS = 3


def load_eval_questions(path: Path = EVAL_QUESTIONS_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_retrieval(
    index: DocumentIndex | None = None,
    questions: list[dict[str, Any]] | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Measure retrieval quality (Hit@1 and Hit@k) separately from answer generation."""
    idx = index or DocumentIndex.load_or_build(DOCS_DIR, INDEX_PATH)
    items = questions if questions is not None else load_eval_questions()

    doc_cases = []
    hits_at_1 = 0
    hits_at_k = 0

    for item in items:
        expected_docs = [
            s for s in item["expected_sources"] if re.search(r"#chunk-\d+$", s)
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


def score_answer(answer: str, rubric: list[dict[str, str]]) -> dict[str, Any]:
    """Check observable facts; regex coverage is not a semantic correctness judge."""
    checks = [
        {
            "fact": fact["fact"],
            "passed": bool(re.search(fact["pattern"], answer, re.I | re.S)),
        }
        for fact in rubric
    ]
    return {
        "fact_checks": checks,
        "fact_coverage": sum(c["passed"] for c in checks) / len(checks)
        if checks
        else None,
        "all_facts_present": all(c["passed"] for c in checks) if checks else None,
        "manual_review_required": True,
    }


def evaluate_agent_set(
    client: ModelClient,
    trace_dir: Path = Path("traces/eval"),
    questions: list[dict[str, Any]] | None = None,
    today: date = DEFAULT_TODAY,
    max_total_cost_usd: float = 0.25,
) -> dict[str, Any]:
    """Run production behavior, keeping labels exclusively in post-run scoring."""
    idx = DocumentIndex.load_or_build(DOCS_DIR, INDEX_PATH)
    items = questions if questions is not None else load_eval_questions()
    trace_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=trace_dir))
    results = []
    spent = 0.0
    for item in items:
        remaining = max_total_cost_usd - spent
        if remaining <= 0:
            break
        print(
            f"Evaluating {item['id']} (remaining budget ${remaining:.4f})", flush=True
        )
        cal_path = working_copy(SOURCE_CALENDAR, run_dir / f"{item['id']}.ics")
        trace_path = run_dir / f"{item['id']}.jsonl"
        run_res = run_agent(
            goal=item["question"],
            client=client,
            tools=CalendarTools.from_file(cal_path, doc_index=idx),
            trace_path=trace_path,
            today=today,
            max_steps=EVAL_MAX_STEPS,
            max_cost_usd=min(EVAL_MAX_COST_PER_QUESTION_USD, remaining),
            max_clarifications=EVAL_MAX_CLARIFICATIONS,
        )
        spent += run_res.cost_usd
        expected_status = (
            "success"
            if item["expected_outcome"] == "answered"
            else item["expected_outcome"]
        )
        cited_ok = (
            set(item["expected_sources"]).issubset(run_res.citations)
            if item["expected_outcome"] == "answered"
            else None
        )
        results.append(
            {
                "id": item["id"],
                "expected_status": expected_status,
                "actual_status": run_res.status,
                "status_ok": run_res.status == expected_status,
                "expected_sources": item["expected_sources"],
                "actual_citations": run_res.citations,
                "citations_ok": cited_ok,
                "steps": run_res.steps,
                "cost_usd": run_res.cost_usd,
                "answer": run_res.answer,
                "trace": _portable_path(trace_path),
                **score_answer(run_res.answer, item.get("answer_rubric", [])),
            }
        )
    citations = [r["citations_ok"] for r in results if r["citations_ok"] is not None]
    coverage = [r["fact_coverage"] for r in results if r["fact_coverage"] is not None]
    return {
        "total_questions": len(results),
        "planned_questions": len(items),
        "complete": len(results) == len(items),
        "outcome_accuracy": mean(r["status_ok"] for r in results) if results else None,
        "citation_accuracy": mean(citations) if citations else None,
        "fact_coverage": mean(coverage) if coverage else None,
        "all_facts_accuracy": mean(
            r["all_facts_present"]
            for r in results
            if r["all_facts_present"] is not None
        )
        if coverage
        else None,
        "cost_usd": spent,
        "results": results,
        "limitations": (
            "Deterministic regex fact coverage only; manual review must check "
            "negation, contradictions, invented claims and semantic correctness. "
            "Cost limit is checked after each provider call and may overshoot "
            "by one call."
        ),
    }


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(Path.cwd()):
        return resolved.relative_to(Path.cwd()).as_posix()
    return path.as_posix()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly enable paid answer generation"
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("traces/evaluation"))
    parser.add_argument("--max-cost-usd", type=float, default=0.25)
    args = parser.parse_args()
    if args.repeats < 1 or args.max_cost_usd <= 0:
        parser.error("repeats and max cost must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="evaluation-", dir=args.output_dir))
    retrieval = evaluate_retrieval()
    (output / "retrieval.json").write_text(
        json.dumps(retrieval, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output_dir": _portable_path(output),
                "retrieval": {k: v for k, v in retrieval.items() if k != "cases"},
            }
        ),
        flush=True,
    )
    if not args.live:
        return
    from agent_project.llm import OpenRouterClient

    model = "google/gemma-4-26b-a4b-it"
    client = OpenRouterClient(model=model, temperature=0)
    repetitions = []
    for repetition in range(args.repeats):
        remaining = args.max_cost_usd - sum(r["cost_usd"] for r in repetitions)
        if remaining <= 0:
            break
        result = evaluate_agent_set(
            client, output / f"repeat-{repetition + 1}", max_total_cost_usd=remaining
        )
        repetitions.append(result)
        metrics = (
            "outcome_accuracy",
            "citation_accuracy",
            "fact_coverage",
            "all_facts_accuracy",
        )
        aggregate = {}
        for metric in metrics:
            values = [r[metric] for r in repetitions if r[metric] is not None]
            aggregate[metric] = {
                "mean": mean(values) if values else None,
                "worst": min(values) if values else None,
            }
        report = {
            "model": model,
            "today": DEFAULT_TODAY.isoformat(),
            "requested_repeats": args.repeats,
            "complete": len(repetitions) == args.repeats
            and all(r["complete"] for r in repetitions),
            "cost_usd": sum(r["cost_usd"] for r in repetitions),
            "aggregate": aggregate,
            "repetitions": repetitions,
        }
        (output / "answers.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps({k: v for k, v in report.items() if k != "repetitions"}),
            flush=True,
        )
    if not report["complete"]:
        parser.exit(1, "Evaluation incomplete: the cost budget was exhausted.\n")


if __name__ == "__main__":
    main()
