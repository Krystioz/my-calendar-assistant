# Calendar Assistant Agent

Student project for **Designing LLM/VLM Agents**. An agent that answers
questions about your calendar — availability, conflicts, and past events — by
choosing which calendar tools to call and reacting to what they return.

## Setup

```bash
git clone <your-repository-url>
cd <your-repository-directory>
uv sync --locked
cp .env.example .env
uv run pytest
```

Add your OpenRouter key to `.env`. Never commit this file or place the key in
code, traces, issues, or reports. Check the connection:

```bash
uv run python -c 'from agent_project.llm import OpenRouterClient; print(OpenRouterClient().complete([{"role": "user", "content": "Reply with exactly COURSE_READY"}])["text"])'
```

## Usage

```bash
uv run calendar-agent "Can I go to the Python meetup on Friday 23.10, 18:00-20:00?" --today 2026-10-16
```

The agent prints its answer and a status line with the number of steps, the
cost, and the trace file. Options:

- `--calendar PATH` — calendar file (default `data/calendar.ics`);
- `--today YYYY-MM-DD` — fixed "today" for reproducible runs;
- `--trace PATH` — trace file (default `traces/run-<timestamp>.jsonl`).

The exit code is `0` only when the run ends with status `success`.

## Project Brief

- **Project title:** Calendar Assistant Agent.
- **Intended user:** a student or anyone with a busy personal calendar who
  wants quick answers without scrolling through weeks of events.
- **Problem and primary task:** answer questions such as "Am I free on Friday
  at 18:00, and if not, can I move what conflicts?" or "When was I last at the
  dentist and how long did it take?". Later the agent will also read posters,
  timetables and email screenshots and propose new events.
- **Why an agent is appropriate:** the next step depends on what the calendar
  returns. A conflict leads to checking alternatives; an empty search leads to
  a different keyword (entries are in Polish, questions may be in English); a
  missing entry leads to an explicit "not found" instead of a guess. A fixed
  pipeline cannot make these choices.
- **Planned data:** a local `.ics` calendar (`data/calendar.ics`, 22 fictional
  events) and, later, documents such as syllabi and schedules. A real Google
  Calendar is a later backend; evaluation always uses the fixed fictional file.
- **Planned tools:** read-only `search_events` and `free_busy` (now); later a
  reversible `create_event` in a separate agent calendar and sensitive
  `delete_event` / invitations that require user confirmation, exposed also
  through a local MCP server.
- **Planned visual input:** a photo of a poster, a class timetable, or an email
  screenshot with a date — the agent reads it with a tool and checks it against
  the calendar.
- **Success criteria:** answers cite only events returned by tools; conflicts
  are detected; questions with no matching data end with outcome `not_found`;
  every run stays within 12 model steps and USD 0.10, and is fully traced.
- **Non-goals and safety boundaries:** no changes to the calendar at this
  stage; no sending of invitations or emails without explicit confirmation;
  event titles and descriptions are treated as data, never as instructions;
  real calendar data never goes into `evidence/`.

A good result for "Can I go to the meetup on 23.10 at 18:00?" is: "No — your
Operating Systems lab is 17:30–19:00. The lab can be made up with group 1 on
Wednesday 21.10, 12:00–13:30, and you are free then."

## How the Agent Works

```text
goal -> model chooses a tool -> code checks the request -> code runs the tool
     -> result goes back to the model -> model decides again or calls finish
```

- **The model only proposes.** Every tool request is checked in
  `execute_tool_call` (`src/agent_project/tools.py`): the tool must exist, the
  arguments must be a JSON object that passes the Pydantic model (date ranges,
  result limits). A rejected request is returned to the model as an error
  observation (`unknown_tool`, `invalid_json`, `invalid_arguments`,
  `tool_failed`) so it can correct itself.
- **The run ends** (`src/agent_project/agent.py`) when the model calls
  `finish` with outcome `answered` (status `success`) or `not_found`, replies
  without a tool call (`success`), the model call fails or three tool requests
  in a row are rejected (`error`), after 12 steps (`step_limit`), or when the
  cost exceeds USD 0.10 (`cost_limit`).
- **Every run is traced** as JSON Lines: `run_start`, one `model_call` per step
  (model, provider, finish reason, latency, tokens, cost, requested tools),
  one `tool_result` per tool call (arguments, result or error), and `run_end`
  (status, answer, steps, total tokens, cost, wall time).

## Repository Layout

- `src/agent_project/tools.py` — calendar loading, read-only tools, request
  checks;
- `src/agent_project/agent.py` — the bounded agent loop and tracing;
- `src/agent_project/cli.py` — command line entry point;
- `src/agent_project/llm.py`, `tracing.py` — course starter code (OpenRouter
  client, mock client, trace helper);
- `data/calendar.ics` — fictional sample calendar;
- `tests/` — tool tests and agent-loop tests on the mock client;
- `traces/` — generated traces, ignored by Git;
- `evidence/` — selected traces for checkpoints.

## Course Limits

- One model for all live work: `google/gemma-4-26b-a4b-it`.
- USD 2 per key for the whole course, enforced by OpenRouter.
- In your own code: at most 12 model steps and USD 0.10 per agent run.

## Working with the Course Model

- The model usually requests one tool call per turn. Tell it in the system
  prompt that independent calls may be requested together.
- For typed output, such as a plan or facts read from an image, offer one tool
  whose parameters are your Pydantic schema and validate its arguments. Keep
  `tool_choice` at its default, `"auto"`, and give lists a maximum length;
  free-form JSON output sometimes repeats itself until the token limit.
- Results vary between identical runs. Run your fixed evaluation set at least
  three times and report the worst run as well as the average.
- A typical agent run costs well under USD 0.01. Use the mock client in tests.
- Copy the traces you submit to `evidence/`. Check them first for keys,
  personal data, and absolute file paths.

## Commands

```bash
uv run pytest
uv run ruff check .
```

Use the assignments published on the course website as the source of truth.
