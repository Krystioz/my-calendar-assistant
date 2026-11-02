"""Command line entry point: `uv run calendar-agent "your question"`."""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from agent_project.agent import run_agent
from agent_project.llm import OpenRouterClient
from agent_project.tools import WORKING_CALENDAR, CalendarTools, working_copy


def ask_user(question: str) -> bool:
    """Real confirmation: only an explicit yes typed in the terminal counts."""
    answer = input(f"\n{question} [y/N] ").strip().lower()
    return answer in ("y", "yes", "t", "tak")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask the calendar agent a question.")
    parser.add_argument("goal", help="What you want to know or change.")
    parser.add_argument(
        "--calendar",
        type=Path,
        default=WORKING_CALENDAR,
        help="Working calendar the agent reads and changes; created from "
        "data/calendar.ics if missing. Delete it to start over.",
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

    trace_path = args.trace or Path(
        "traces", f"run-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    )
    sys.stdout.reconfigure(encoding="utf-8")
    result = run_agent(
        goal=args.goal,
        client=OpenRouterClient(),
        tools=CalendarTools.from_file(working_copy(target=args.calendar)),
        trace_path=trace_path,
        today=args.today,
        confirm=ask_user,
    )
    print(result.answer)
    print(
        f"\n[status={result.status} steps={result.steps} "
        f"cost=${result.cost_usd:.5f} trace={result.trace_path.as_posix()}]"
    )
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
