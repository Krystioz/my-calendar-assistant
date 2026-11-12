"""Command line entry point: `uv run calendar-agent "your question"`."""

import argparse
import sys
import tomllib
from datetime import date, datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from agent_project.agent import ConversationError, run_agent
from agent_project.config import CONFIG_PATH, load_settings
from agent_project.llm import OpenRouterClient
from agent_project.tools import WORKING_CALENDAR, open_calendar


def ask_user(question: str) -> bool:
    """Real confirmation: only an explicit yes typed in the terminal counts."""
    answer = input(f"\n{question} [y/N] ").strip().lower()
    return answer in ("y", "yes", "t", "tak")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask the calendar agent a question. Settings come from "
        f"{CONFIG_PATH}."
    )
    parser.add_argument(
        "goal",
        help="What you want to know or change; with --thread, your answer to "
        "the agent's question.",
    )
    parser.add_argument(
        "--thread",
        help="Continue the conversation with this id: it must be waiting for "
        "your answer. Pass the same --calendar as in that conversation.",
    )
    parser.add_argument(
        "--calendar",
        help="'google' for Google Calendar and Tasks, or a working calendar file "
        "the agent reads and changes; created from data/calendar.ics if missing. "
        f"Default: CALENDAR_BACKEND from .env, else {WORKING_CALENDAR.as_posix()}.",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
        help="Override today's date (YYYY-MM-DD) for reproducible runs.",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        help="Trace file (default: traces/run-<timestamp>.jsonl).",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings(CONFIG_PATH)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        print(f"[config] {CONFIG_PATH}: {exc}", file=sys.stderr)
        return 2

    trace_path = args.trace or Path(
        "traces", f"run-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
    )
    db = Path(settings.storage.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        with SqliteSaver.from_conn_string(str(db)) as checkpointer:
            result = run_agent(
                goal=args.goal,
                client=OpenRouterClient(
                    model=settings.model.name,
                    temperature=settings.model.temperature,
                ),
                tools=open_calendar(args.calendar),
                trace_path=trace_path,
                today=args.today,
                max_steps=settings.limits.max_steps,
                max_cost_usd=settings.limits.max_cost_usd,
                max_clarifications=settings.limits.max_clarifications,
                confirm=ask_user,
                require_citations=settings.agent.require_citations,
                checkpointer=checkpointer,
                resume_thread=args.thread,
                settings=settings.model_dump(),
            )
    except ConversationError as exc:
        print(f"[thread] {exc}", file=sys.stderr)
        return 2

    print(result.answer)
    print(
        f"\n[status={result.status} steps={result.steps} "
        f"cost=${result.cost_usd:.5f} thread={result.thread_id} "
        f"trace={result.trace_path.as_posix()}]"
    )
    if result.diagram_paths:
        svg, mermaid = result.diagram_paths
        print(f"[diagram={svg.as_posix()} mermaid={mermaid.as_posix()}]")
    if result.status == "clarify":
        print(f'[answer with: calendar-agent "..." --thread {result.thread_id}]')
        return 0
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
