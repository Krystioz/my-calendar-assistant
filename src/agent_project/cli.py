"""Command line entry point: `uv run calendar-agent "your question"`."""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from agent_project.agent import run_agent
from agent_project.llm import OpenRouterClient
from agent_project.tools import CalendarTools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask the calendar agent a question.")
    parser.add_argument("goal", help="What you want to know about your calendar.")
    parser.add_argument("--calendar", type=Path, default=Path("data/calendar.ics"))
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
    result = run_agent(
        goal=args.goal,
        client=OpenRouterClient(),
        tools=CalendarTools.from_file(args.calendar),
        trace_path=trace_path,
        today=args.today,
    )
    sys.stdout.reconfigure(encoding="utf-8")
    print(result.answer)
    print(
        f"\n[status={result.status} steps={result.steps} "
        f"cost=${result.cost_usd:.5f} trace={result.trace_path.as_posix()}]"
    )
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
