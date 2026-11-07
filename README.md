# Calendar Assistant Agent

Student project for the **Designing LLM/VLM Agents** course. An agent that
answers questions about a personal calendar — availability, conflicts, and past
events — by choosing which calendar tools to call and reacting to what they
return.

## Setup

```bash
git clone <repository-url>
cd my-calendar-assistant
uv sync --locked
cp .env.example .env
uv run pytest
```

Put an OpenRouter API key in `.env`. The file is ignored by Git; the key must
never appear in code, traces, issues, or reports. To check the connection:

```bash
uv run python -c 'from agent_project.llm import OpenRouterClient; print(OpenRouterClient().complete([{"role": "user", "content": "Reply with exactly COURSE_READY"}])["text"])'
```

## Usage

```bash
uv run calendar-agent "Can I go to the Python meetup on Friday 23.10, 18:00-20:00?" --today 2026-10-16
```

```bash
uv run calendar-agent "Add the gym on Monday 19.10, 18:00-19:30." --today 2026-10-16
uv run calendar-agent "Delete my maths tutoring on 22.10." --today 2026-10-16
```

The agent prints its answer and a status line with the number of steps, the
cost, and the trace file. Before any deletion the terminal asks
`Delete '...'? [y/N]`; only an explicit `y` deletes. Options:

- `--calendar PATH` — working calendar the agent reads and changes (default
  `workspace/calendar.ics`, copied from `data/calendar.ics` on first use);
- `--today YYYY-MM-DD` — fixed "today" for reproducible runs;
- `--trace PATH` — trace file (default `traces/run-<timestamp>.jsonl`).

The exit code is `0` only when the run ends with status `success`.
`data/calendar.ics` never changes; delete `workspace/calendar.ics` to start
over from the original.

### MCP server

The same tools are available to any MCP host (Claude Code, Claude Desktop,
MCP Inspector) through a local stdio server:

```bash
uv run calendar-mcp
uv run mcp dev src/agent_project/mcp_server.py
```

The second command opens the MCP Inspector (needs `npx`). The server uses the
same working calendar as the CLI. It has no way to ask the user, so
`delete_event` over MCP always returns `confirmation_required` and deletes
nothing.

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
- **Tools:** read-only `search_events` and `free_busy`, reversible
  `create_event`, and sensitive `delete_event` that needs the user's
  confirmation; all four are also exposed through a local MCP server.
- **Planned visual input:** a photo of a poster, a class timetable, or an email
  screenshot with a date — the agent reads it with a tool and checks it against
  the calendar.
- **Success criteria:** answers cite only events returned by tools; conflicts
  are detected; questions with no matching data end with outcome `not_found`;
  every run stays within 12 model steps and USD 0.10, and is fully traced.
- **Non-goals and safety boundaries:** the agent works on a local copy, never
  on the source data; nothing is deleted without an explicit yes from the user;
  no invitations or emails;
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
  result limits), and a sensitive tool must get a real confirmation from the
  user. A rejected request is returned to the model as an error observation
  (`unknown_tool`, `invalid_json`, `invalid_arguments`, `tool_failed`) so it
  can correct itself.

| Tool | Type | Checked by the application |
|---|---|---|
| `search_events` | read-only | keywords, date range, result limit |
| `free_busy` | read-only | window order, at most 14 days |
| `create_event` | reversible write | title and times, at most 24 h; `already_exists` for the same title at the same start; overlaps returned as `conflicts` |
| `delete_event` | sensitive | event exists (`not_found`); the user answers `y` in the terminal, otherwise `declined_by_user`; without a way to ask (MCP) `confirmation_required` |

Domain outcomes such as `already_exists` or `declined_by_user` come back to the
model as observations and change what it does next: it reports the duplicate
instead of adding it again, and it does not retry a declined deletion.

```mermaid
flowchart TD
    M(["Model (Gemma via OpenRouter)<br/>may only request a tool call"])
    subgraph APP["Python application decides"]
        V{"Known tool?<br/>Valid arguments?"}
        S{"Sensitive tool?"}
        C{"User typed y?"}
        X["Run tool on the<br/>working calendar"]
        R["Observation back to the model<br/>ok, already_exists, not_found,<br/>declined_by_user, confirmation_required"]
    end
    U(["User in the terminal"])
    MCP(["MCP host"])
    M -->|"tool call"| V
    MCP -->|"same tool call"| V
    V -->|"no"| R
    V -->|"yes"| S
    S -->|"no"| X
    S -->|"yes"| C
    U -.->|"y / N"| C
    C -->|"yes"| X
    C -->|"no, or no way to ask"| R
    X --> R
    R --> M
```
- **The run ends** (`src/agent_project/agent.py`) when the model calls
  `finish` with outcome `answered` (status `success`) or `not_found`, replies
  without a tool call (`success`), the model call fails or three tool requests
  in a row are rejected (`error`), after 12 steps (`step_limit`), or when the
  cost exceeds USD 0.10 (`cost_limit`).
- **Every run is traced** as JSON Lines: `run_start`, one `model_call` per step
  (model, provider, finish reason, latency, tokens, cost, requested tools),
  one `tool_result` per tool call (arguments, result or error), one
  `confirmation` per question asked to the user (question and answer), and
  `run_end` (status, answer, steps, total tokens, cost, wall time).

## Repository Layout

- `src/agent_project/tools.py` — calendar loading and saving, the four tools,
  request checks and the confirmation rule;
- `src/agent_project/agent.py` — the bounded agent loop and tracing;
- `src/agent_project/cli.py` — command line entry point and the `y/N` prompt;
- `src/agent_project/mcp_server.py` — local MCP server over the same tools;
- `src/agent_project/llm.py`, `tracing.py` — course starter code (OpenRouter
  client, mock client, trace helper);
- `data/calendar.ics` — fictional sample calendar;
- `workspace/` — working copy of the calendar, ignored by Git;
- `tests/` — tool tests, agent-loop tests on the mock client, MCP server tests;
- `traces/` — generated traces, ignored by Git;
- `evidence/` — selected traces for checkpoints.

## Model and Limits

- All live runs use one model: `google/gemma-4-26b-a4b-it` via OpenRouter.
- Each agent run is capped at 12 model steps and USD 0.10; a typical run costs
  well under USD 0.01.
- Tests use the mock client and make no API calls.

## Design Notes

- The model usually requests one tool call per turn, so the system prompt
  states that independent calls may be requested together.
- Typed output (a plan, facts read from an image) goes through a tool whose
  parameters are a Pydantic schema, and its arguments are validated.
  `tool_choice` stays at `"auto"` and lists have a maximum length, because
  free-form JSON output sometimes repeats itself until the token limit.
- Results vary between identical runs, so the evaluation set is run at least
  three times and both the worst run and the average are reported.
- Traces in `evidence/` are checked for keys, personal data, and absolute file
  paths before they are committed.

## Commands

```bash
uv run pytest
uv run ruff check .
```
