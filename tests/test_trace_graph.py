import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agent_project.trace_graph import export_trace, load_nodes, main


def write_trace(tmp_path, *events):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        "\n".join(json.dumps({"event": e, "payload": p}) for e, p in events),
        encoding="utf-8",
    )
    return path


def test_export_preserves_retries_rejection_sources_and_outcome(tmp_path):
    trace = write_trace(
        tmp_path,
        ("run_start", {"goal": "Czy mogę odrobić laboratorium?"}),
        ("model_call", {"step": 1, "tool_calls": []}),
        (
            "tool_result",
            {
                "step": 1,
                "name": "search_docs",
                "ok": True,
                "result": {"fragments": [{"id": "rules.md#chunk-1", "score": 0.8}]},
            },
        ),
        ("graph_node", {"node": "retrieve_docs", "next_node": "call_model"}),
        (
            "tool_result",
            {
                "step": 2,
                "name": "finish",
                "ok": False,
                "error": {"code": "unverified_citations", "message": "Retry"},
            },
        ),
        ("tool_result", {"step": 3, "name": "search_docs", "ok": True}),
        ("confirmation", {"question": "Delete?", "approved": False}),
        ("run_end", {"status": "not_found", "answer": "Brakuje zasad odrabiania."}),
    )
    before = trace.read_bytes()
    svg, mermaid = export_trace(trace)
    assert trace.read_bytes() == before
    root = ET.fromstring(svg.read_text("utf-8"))
    text = " ".join(root.itertext())
    assert text.index("Tura LLM 1 · wynik obsługi narzędzia: search_docs") < text.index(
        "Tura LLM 3 · wynik obsługi narzędzia: search_docs"
    )
    for expected in (
        "rules.md#chunk-1",
        "unverified_citations",
        "ODMOWA",
        "retrieve_docs",
        "KONIEC · not_found",
    ):
        assert expected in text
        assert expected.replace("#", "#35;") in mermaid.read_text("utf-8")
    assert "Legenda" in mermaid.read_text("utf-8")
    assert "n6 --> n7" in mermaid.read_text("utf-8")


def test_labels_distinguish_llm_response_from_completed_graph_node(tmp_path):
    trace = write_trace(
        tmp_path,
        (
            "model_call",
            {
                "step": 4,
                "model": "test-model",
                "tool_calls": [{"name": "search_docs", "arguments": {"query": "lab"}}],
            },
        ),
        ("graph_node", {"step": 4, "node": "call_model", "next_node": "call_model"}),
    )
    nodes, _ = load_nodes(trace)
    response, control = nodes
    assert response.title == "Tura LLM 4 · odebrano odpowiedź LLM"
    assert "Propozycja wywołania narzędzia: search_docs" in response.details[2]
    assert control.title == "Tura LLM 4 · zakończono węzeł: obsługa tury LLM"
    assert "Identyfikator w kodzie: call_model" in control.details
    assert "Przejście do: obsługa tury LLM (call_model)" in control.details


def test_export_escapes_untrusted_text_and_redacts_structured_secrets(tmp_path):
    malicious = '<script>alert("x")</script> & A["injected"] --> B'
    trace = write_trace(
        tmp_path,
        ("run_start", {"goal": malicious}),
        (
            "model_call",
            {"tool_calls": [{"name": "search", "arguments": {"api_key": "secret"}}]},
        ),
        ("run_end", {"status": "success", "answer": malicious}),
    )
    svg, mermaid = export_trace(trace)
    root = ET.fromstring(svg.read_text("utf-8"))
    assert not list(root.iter("{http://www.w3.org/2000/svg}script"))
    assert malicious in " ".join(root.itertext())
    for path in (svg, mermaid):
        content = path.read_text("utf-8")
        assert "secret" not in content
        assert "REDACTED" in content
    assert 'A["injected"] --> B' not in mermaid.read_text("utf-8")


def test_old_partial_log_is_labeled_without_inventing_graph_nodes(tmp_path):
    trace = write_trace(tmp_path, ("model_error", {"error": "Timeout"}))
    svg, mermaid = export_trace(trace)
    assert "NIEPEŁNY LOG" in svg.read_text("utf-8")
    assert "NIEPEŁNY LOG" in mermaid.read_text("utf-8")
    nodes, complete = load_nodes(trace)
    assert not complete
    assert [n.kind for n in nodes] == ["error"]


@pytest.mark.parametrize("content", ["", "{oops", "[]", '{"event":"start"}'])
def test_invalid_trace_fails_before_writing_outputs(tmp_path, content):
    trace = tmp_path / "bad.jsonl"
    trace.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        export_trace(trace)
    assert not trace.with_suffix(".svg").exists()


def test_multiple_runs_are_not_silently_joined(tmp_path):
    trace = write_trace(tmp_path, ("run_start", {}), ("run_start", {}))
    with pytest.raises(ValueError, match="Multiple runs"):
        export_trace(trace)


def test_cli_exports_existing_trace_to_chosen_directory(tmp_path, capsys):
    trace = write_trace(tmp_path, ("run_start", {}), ("run_end", {"status": "clarify"}))
    output = tmp_path / "presentation" / "demo"
    assert main([str(trace), "--output", str(output)]) == 0
    assert output.with_suffix(".svg").exists()
    assert output.with_suffix(".mmd").exists()
    assert str(output.with_suffix(".svg")) in capsys.readouterr().out


def test_export_does_not_overwrite_input_with_diagram_suffix(tmp_path):
    trace = write_trace(tmp_path, ("run_start", {}))
    svg_input = trace.rename(Path(tmp_path / "trace.svg"))
    before = svg_input.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        export_trace(svg_input)
    assert svg_input.read_bytes() == before


def test_calendar_cli_automatically_exports_even_when_agent_abstains(
    tmp_path, monkeypatch, capfd
):
    from agent_project import MockClient, cli

    config = tmp_path / "calendar-agent.toml"
    config.write_text(
        cli.CONFIG_PATH.read_text("utf-8").replace(
            "workspace/conversations.sqlite", (tmp_path / "c.sqlite").as_posix()
        ),
        "utf-8",
    )
    monkeypatch.setattr(cli, "CONFIG_PATH", config)
    monkeypatch.setattr(
        cli,
        "OpenRouterClient",
        lambda **_: MockClient(
            [
                {
                    "tool_calls": [
                        {
                            "id": "finish",
                            "name": "finish",
                            "arguments": {"outcome": "not_found", "answer": "Missing"},
                        }
                    ]
                }
            ]
        ),
    )
    trace = tmp_path / "cli.jsonl"
    assert (
        cli.main(
            [
                "demo",
                "--calendar",
                str(tmp_path / "calendar.ics"),
                "--trace",
                str(trace),
            ]
        )
        == 1
    )
    assert (tmp_path / "diagrams" / "cli.svg").exists()
    assert (tmp_path / "diagrams" / "cli.mmd").exists()
    assert "diagram=" in capfd.readouterr().out
    start = json.loads(trace.read_text("utf-8").splitlines()[0])["payload"]
    assert start["settings"]["model"]["name"]


@pytest.mark.parametrize("outcome", ["answered", "not_found", "clarify", "error"])
def test_python_agent_run_exports_automatically_for_each_outcome(tmp_path, outcome):
    from datetime import date

    from agent_project import MockClient
    from agent_project.agent import run_agent
    from agent_project.tools import CalendarTools

    replies = (
        []
        if outcome == "error"
        else [
            {
                "tool_calls": [
                    {
                        "id": "finish",
                        "name": "finish",
                        "arguments": {"outcome": outcome, "answer": "Demo"},
                    }
                ]
            }
        ]
    )
    trace = tmp_path / "run.jsonl"
    result = run_agent(
        "Demo",
        MockClient(replies),
        CalendarTools([]),
        trace,
        date(2026, 10, 16),
        max_steps=12,
        max_cost_usd=0.10,
        max_clarifications=3,
    )
    assert result.status == ("success" if outcome == "answered" else outcome)
    assert result.diagram_paths == (
        tmp_path / "diagrams" / "run.svg",
        tmp_path / "diagrams" / "run.mmd",
    )
    assert all(path.exists() for path in result.diagram_paths)
    assert f"KONIEC · {result.status}" in result.diagram_paths[0].read_text("utf-8")


def test_diagram_write_failure_keeps_the_agent_result(tmp_path, monkeypatch, caplog):
    from agent_project.agent import _Run

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("agent_project.agent.export_trace", fail)
    result = _Run(tmp_path / "run.jsonl").end("success", "Done")
    assert result.status == "success"
    assert result.answer == "Done"
    assert result.diagram_paths is None
    assert "disk full" in caplog.text
