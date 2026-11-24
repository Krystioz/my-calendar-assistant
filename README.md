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
uv run python -c 'from agent_project.llm import OpenRouterClient; print(OpenRouterClient(model="google/gemma-4-26b-a4b-it", temperature=0).complete([{"role": "user", "content": "Reply with exactly COURSE_READY"}])["text"])'
```

## Usage

```bash
uv run calendar-agent "Can I go to the Python meetup on Friday 23.10, 18:00-20:00?" --today 2026-10-16
```

The agent prints its answer, the number of steps, the cost, and the trace file.
Deletions ask `Delete '...'? [y/N]` first. When the agent needs more details it
asks a question; answer with `--thread <id>`. Every run also writes SVG and
Mermaid diagrams of its path to `traces/diagrams/`. The agent works on
`workspace/calendar.ics`, a copy of `data/calendar.ics`; settings live in
`calendar-agent.toml`. Add `--image poster.png` (PNG, JPEG or WebP, up to
5 MB) to let the agent read a poster or a timetable; in the chat UI use the
paperclip or Ctrl+V.

More:

- [CLI usage](#cli-reference) — options, config, clarifying questions, exit
  codes, traces, run diagrams;
- [Integrations](#integrations) — LangGraph Studio, chat UI (`ui/`), MCP
  server, Google Calendar and Google Tasks;
- [Evaluation](#evaluation) — M3 checkpoint, citation checks, design notes;
- [Vision and safety report](evidence/vision-safety/report.html) — image path,
  typed action and its checks, eight fixed cases (prompt injection, tool
  failure, unauthorized delete), runs before and after the fixes; rerun with
  `uv run python -m agent_project.eval_vision_safety --repeats 3`.

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
- **Data:** a local `.ics` calendar (`data/calendar.ics`, 22 fictional
  events) and six fictional documents in `data/docs/` (19 indexed fragments).
  Google Calendar is an optional backend; evaluation always uses the fixed
  fictional file.
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

**The model only proposes.** Every tool request is checked in
`execute_tool_call` (`src/agent_project/tools.py`): the tool must exist, the
arguments must pass the Pydantic model, and a sensitive tool must get a real
confirmation from the user. A rejected request or a domain outcome
(`already_exists`, `declined_by_user`, ...) goes back to the model as an
observation, so it can correct itself.

| Tool | Type | Checked by the application |
|---|---|---|
| `search_events` | read-only | keywords, date range, result limit |
| `free_busy` | read-only | window order, at most 14 days |
| `create_event` | reversible write | title and times, at most 24 h; `already_exists` for the same title at the same start; overlaps returned as `conflicts` |
| `delete_event` | sensitive | event exists (`not_found`); the user answers `y` in the terminal, otherwise `declined_by_user`; without a way to ask (MCP) `confirmation_required` |

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

**The run ends** (`src/agent_project/agent.py`) when the model calls `finish`
with outcome `answered` (status `success`), `not_found`, or `clarify`. A plain
text reply is sent back with a request to call `finish`; a second plain reply
in a row is taken as `finish` with outcome `answered`, through the same checks. Technical exits: the model call fails or three tool requests
in a row are rejected (`error`), 12 steps (`step_limit`), or more than USD 0.10
(`cost_limit`).

## Model and Limits

- All live runs use `google/gemma-4-26b-a4b-it` via OpenRouter.
- A typical run costs well under USD 0.01.
- Tests use the mock client and make no API calls.

## Commands

```bash
uv run pytest
uv run ruff check .
```

## CLI reference

Run commands from the repository root. On Windows with an existing environment,
use `.venv\Scripts\python.exe -m agent_project.cli` in place of
`uv run calendar-agent`; no environment rebuild is needed. For the setup above,
PowerShell users can copy the template with `Copy-Item .env.example .env`.

| Argument | Meaning |
|---|---|
| `goal` | Question or requested change; when resuming, the answer to the agent's question |
| `--today YYYY-MM-DD` | Fixed reference date; defaults to the current date |
| `--calendar PATH` | Working calendar; copied from `data/calendar.ics` if missing |
| `--calendar google` | Use Google Calendar and Tasks instead of the local file |
| `--thread ID` | Resume a conversation waiting for clarification; pass the same calendar |
| `--trace PATH` | JSONL output; defaults to `traces/run-<timestamp>.jsonl` |
| `--image PATH` | PNG/JPEG/WebP up to 5 MB; new conversations only |

```powershell
.venv\Scripts\python.exe -m agent_project.cli "Can I attend the meetup on 23 October at 18:00?" --today 2026-10-16 --calendar workspace/demo.ics
.venv\Scripts\python.exe -m agent_project.cli "From 18:00 to 20:00" --thread <id-from-output> --calendar workspace/demo.ics
```

The second command applies only after a `clarify` result. Conversations are stored
in the SQLite file configured by `storage.db`. Exit code 0 means `success` or
`clarify`; 1 means another agent outcome (including `not_found` and limits);
2 means an argument, configuration, conversation, or input error.

`calendar-agent.toml` controls the model, temperature, conversation database,
citation requirement and per-turn limits (12 model calls, USD 0.10, three
clarifications). All keys are required. The cost check follows a model response,
so the budget can be exceeded by one call. Traces record tool calls, observations,
status, timing, tokens and cost. SVG/Mermaid run diagrams are saved alongside
them in `diagrams/`. To regenerate a diagram:

```powershell
.venv\Scripts\python.exe -m agent_project.trace_graph traces/example.jsonl
```

## Integrations

The local backend is the default when `CALENDAR_BACKEND` is empty. A CLI
`--calendar` argument overrides that setting. Keep the evaluation on fictional
data; the evaluators create independent copies of the fixture calendar.

For LangGraph Studio and the React chat UI, start the API from the repository
root, then the UI in a second terminal:

```powershell
.venv\Scripts\langgraph.exe dev
```

```powershell
cd ui
npm ci
npm run dev
```

The API uses `langgraph.json` and graph `calendar_agent`, with `.env` loaded by
the server. The UI is at `http://localhost:5173` and connects to
`http://127.0.0.1:2024` by default; set `VITE_API_URL` to override it.
Set `LANGSMITH_TRACING=false` for local use without remote tracing. The UI
supports image attachment and human confirmation interrupts.

For an MCP host, configure a stdio server with the absolute executable path
`<repo>\.venv\Scripts\python.exe`, arguments `-m agent_project.mcp_server`,
and working directory `<repo>`. The equivalent terminal command is:

```powershell
.venv\Scripts\python.exe -m agent_project.mcp_server
```

MCP shares tool validation with the agent. It has no confirmation channel:
`delete_event` and `delete_task` return `confirmation_required`. Event creation
uses the default calendar because the server has no user text to authorize a
named calendar.

For the optional Google backend, enable Calendar API and Tasks API in a Google
Cloud project, create an OAuth Desktop app client, and save its downloaded JSON
as `workspace/credentials.json`. Run the CLI with `--calendar google`; the first
run opens sign-in and saves `workspace/token.json`. Both files stay in the
ignored workspace. Alternatively set `CALENDAR_BACKEND=google` in `.env`;
`GOOGLE_DEFAULT_CALENDAR` selects the default event calendar, otherwise the
primary calendar is used. This backend reads and changes the connected account.

## Evaluation

Use the existing interpreter from the repository root:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m agent_project.eval_rag --output-dir traces/evaluation
```

Tests use mock model responses and need no OpenRouter key. Retrieval evaluation
uses FastEmbed; its first run downloads the embedding model, later runs use
`.cache/fastembed/`. Rebuild the index from `data/docs/` when needed:

```powershell
.venv\Scripts\python.exe -c "from agent_project.rag import DocumentIndex; DocumentIndex.build()"
```

Live evaluation needs `OPENROUTER_API_KEY` in `.env` and incurs API costs:

```powershell
.venv\Scripts\python.exe -m agent_project.eval_rag --live --repeats 3 --max-cost-usd 0.25 --output-dir traces/evaluation
.venv\Scripts\python.exe -m agent_project.eval_vision_safety --repeats 3 --max-total-cost-usd 0.50 --output-dir traces/vision-safety
```

RAG uses ten fixed questions (including three unanswerable ones), separates
retrieval hits from answer/status/citation metrics, and writes `retrieval.json`
plus live answer metrics and JSONL traces into a new evaluation directory.
Vision/safety uses eight fixed cases, fresh calendars and deterministic checks
of traces and calendar changes; `--case poster-no-time` selects one case.
The total budgets are checked between runs and can be exceeded by the final run.
Temperature 0 does not guarantee identical live results.

Read the [RAG checkpoint report](evidence/rag/report.html) for metrics,
citation validation, failure analysis and the citation-hardening addendum.
The [vision and safety report](evidence/vision-safety/report.html) includes
before/after results, limitations and the release recommendation; its
[architecture source](evidence/vision-safety/architecture.mmd) marks the trust
boundary. Reports describe their recorded checkpoint versions, including
historical limitations; they are not fresh evaluations of the current code.
