"""Offline, chronological run diagrams: editable Mermaid and standalone SVG."""

import argparse
import json
import textwrap
from dataclasses import dataclass
from html import escape
from pathlib import Path

from agent_project.tracing import _redact

# Colour is paired with a visible category label in every node.
STYLES = {
    "boundary": ("Start / koniec", "#edf2f7", "#475569"),
    "model": ("Komunikacja z LLM", "#dbeafe", "#1d4ed8"),
    "tool": ("Narzędzie", "#dcfce7", "#15803d"),
    "graph": ("Sterowanie LangGraph", "#f3e8ff", "#7e22ce"),
    "decision": ("Decyzja człowieka", "#fef3c7", "#92400e"),
    "error": ("Błąd / odrzucenie", "#fee2e2", "#b91c1c"),
}

NODE_LABELS = {
    "call_model": "obsługa tury LLM",
    "retrieve_docs": "obsługa żądań z wyszukiwaniem dokumentów",
    "use_calendar_tools": "obsługa żądań narzędzi kalendarza",
    "verify_citations": "weryfikacja cytowań",
    "ask_clarification": "pytanie doprecyzowujące do użytkownika",
    "abstain": "abstencja z powodu braku dowodów",
    "end": "zakończenie przebiegu",
}


@dataclass
class TraceNode:
    kind: str
    title: str
    details: list[str]


def _short(value, limit=240) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _arguments(raw) -> str:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return _short(raw)
    return _short(json.dumps(_redact(raw), ensure_ascii=False))


def load_nodes(path: Path) -> tuple[list[TraceNode], bool]:
    """Accept historical event logs too; never infer unrecorded graph visits."""
    records = []
    for number, line in enumerate(path.read_text("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {number}: invalid JSON") from exc
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("event"), str)
            or not isinstance(record.get("payload"), dict)
        ):
            raise ValueError(f"Line {number}: expected event and payload object")
        records.append(_redact(record))
    if not records:
        raise ValueError("The trace is empty")
    if sum(r["event"] == "run_start" for r in records) > 1:
        raise ValueError("Multiple runs in one file; export one run at a time")

    nodes = []
    for record in records:
        event, p = record["event"], record["payload"]
        step = f"Tura LLM {p['step']} · " if "step" in p else ""
        details = []
        kind = "boundary"
        title = event
        if event == "run_start":
            title = "START · komenda użytkownika"
            details = [_short(p.get("goal", ""))]
        elif event == "model_call":
            kind, title = "model", step + "odebrano odpowiedź LLM"
            details = [
                "Model: " + _short(p.get("model", "")),
                f"Czas żądania: {p.get('latency_ms', '?')} ms · "
                f"koszt narastająco: ${p.get('cost_so_far_usd', 0):.5f}",
            ]
            for call in p.get("tool_calls") or []:
                details.append(
                    f"Propozycja wywołania narzędzia: {call.get('name', '?')} "
                    + _arguments(call.get("arguments"))
                )
            if p.get("text"):
                details.append("Tekst odpowiedzi LLM: " + _short(p["text"]))
        elif event == "tool_result":
            kind = "tool" if p.get("ok") else "error"
            title = step + "wynik obsługi narzędzia: " + str(p.get("name", "?"))
            details = ["Argumenty: " + _arguments(p.get("arguments"))]
            if not p.get("ok"):
                error = p.get("error") or {}
                details.append(_short(f"{error.get('code')}: {error.get('message')}"))
            else:
                result = p.get("result") or {}
                if "fragments" in result:
                    fragments = result["fragments"]
                    details.append(f"Znaleziono fragmentów: {len(fragments)}")
                    details.extend(
                        _short(f"{f.get('id')} · score={f.get('score', '?')}")
                        for f in fragments
                    )
                else:
                    details.append("Wynik: " + _arguments(result))
        elif event == "graph_node":
            node, next_node = p.get("node", "?"), p.get("next_node", "?")
            kind, title = (
                "graph",
                step + "zakończono węzeł: " + NODE_LABELS.get(node, node),
            )
            details = [
                f"Identyfikator w kodzie: {node}",
                f"Przejście do: {NODE_LABELS.get(next_node, next_node)} ({next_node})",
                f"Zebrane źródła: {len(p.get('retrieved_ids', []))} · "
                f"status: {p.get('status') or 'w toku'}",
            ]
        elif event == "confirmation":
            kind, title = "decision", step + "potwierdzenie użytkownika"
            details = [
                _short(p.get("question", "")),
                "ZGODA" if p.get("approved") else "ODMOWA",
            ]
        elif event == "model_error":
            kind, title = "error", step + "błąd żądania do LLM"
            details = [_short(p.get("error", ""))]
        elif event == "run_end":
            status = p.get("status", "?")
            kind = "error" if status in {"error", "step_limit", "cost_limit"} else kind
            title = "KONIEC · " + status
            details = [
                _short(p.get("answer", ""), 600),
                f"Liczba tur LLM: {p.get('steps', '?')} · "
                f"koszt: ${p.get('cost_usd', 0):.5f} · "
                f"czas: {p.get('wall_time_ms', '?')} ms",
            ]
            if p.get("citations"):
                details.append("Cytowania: " + ", ".join(p["citations"]))
        else:
            details = [_arguments(p)]
        if record.get("timestamp"):
            details.append(_short(record["timestamp"]))
        nodes.append(TraceNode(kind, title, details))
    complete = records[0]["event"] == "run_start" and records[-1]["event"] == "run_end"
    return nodes, complete


def _mermaid_text(text: str) -> str:
    return "".join(f"#{ord(c)};" if c in '&"<>[]{}|#`\\' else c for c in text)


def to_mermaid(nodes: list[TraceNode], complete: bool) -> str:
    lines = ["flowchart TD", '  subgraph legend["Legenda"]']
    for kind, (label, _, _) in STYLES.items():
        lines.append(f'    legend_{kind}["{label}"]:::{kind}')
    lines.extend(["  end", f'  note["{_caption(complete)}"]'])
    for i, node in enumerate(nodes):
        label = "<br/>".join(_mermaid_text(t) for t in [node.title, *node.details])
        lines.append(f'  n{i}["{label}"]:::{node.kind}')
        if i:
            lines.append(f"  n{i - 1} --> n{i}")
    for kind, (_, fill, stroke) in STYLES.items():
        lines.append(f"  classDef {kind} fill:{fill},stroke:{stroke},color:#172033")
    return "\n".join(lines) + "\n"


def _caption(complete: bool) -> str:
    return (
        "Przebieg chronologiczny · strzałki = kolejność zdarzeń"
        if complete
        else "NIEPEŁNY LOG · brak początku lub końca przebiegu"
    )


def to_svg(nodes: list[TraceNode], complete: bool) -> str:
    """Render locally with standard-library XML escaping, without external assets."""
    parts = [
        '<title id="title">Przebieg agenta</title>',
        '<desc id="desc">Chronologiczna historia, nie mapa wszystkich możliwych '
        "ścieżek. Treści są skrócone; pełne dane pozostają w logu JSONL.</desc>",
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" '
        'refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" '
        'fill="#64748b"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Segoe UI, sans-serif" fill="#172033">',
        '<text x="28" y="36" font-size="22">Przebieg agenta</text>',
        f'<text x="28" y="62" font-size="14">{escape(_caption(complete))}</text>',
        '<text x="28" y="84" font-size="13">Numer zdarzenia ≠ tura LLM. '
        "Sterowanie LangGraph = zakończony węzeł i wybrane przejście.</text>",
    ]
    for i, (_, (label, fill, stroke)) in enumerate(STYLES.items()):
        x, y = 28 + (i % 3) * 278, 102 + (i // 3) * 30
        parts.append(
            f'<rect x="{x}" y="{y}" width="16" height="16" '
            f'fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{x + 24}" y="{y + 13}" font-size="13">{label}</text>'
        )
    y = 175
    for i, node in enumerate(nodes):
        label, fill, stroke = STYLES[node.kind]
        title_lines = textwrap.wrap(f"{i + 1}. {node.title}", 75)
        detail_lines = [
            line for detail in node.details for line in textwrap.wrap(detail, 96)
        ]
        height = 52 + len(title_lines) * 23 + len(detail_lines) * 19
        if i:
            parts.append(
                f'<line x1="440" y1="{y - 26}" x2="440" y2="{y - 4}" '
                'stroke="#64748b" marker-end="url(#arrow)"/>'
            )
        parts.append(
            f'<rect x="28" y="{y}" width="824" height="{height}" rx="10" '
            f'fill="{fill}" stroke="{stroke}"/>'
            f'<text x="46" y="{y + 22}" font-size="12">{label}</text>'
        )
        text_y = y + 47
        for line in title_lines:
            parts.append(
                f'<text x="46" y="{text_y}" font-size="17" '
                f'font-weight="600">{escape(line)}</text>'
            )
            text_y += 23
        for line in detail_lines:
            parts.append(
                f'<text x="46" y="{text_y}" font-size="14">{escape(line)}</text>'
            )
            text_y += 19
        y += height + 30
    parts.append("</g></svg>")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-labelledby="title desc" width="880" height="{y}" '
        f'viewBox="0 0 880 {y}">' + "\n".join(parts)
    )


def export_trace(trace_path: Path, output: Path | None = None) -> tuple[Path, Path]:
    nodes, complete = load_nodes(trace_path)
    base = output or trace_path
    svg, mermaid = base.with_suffix(".svg"), base.with_suffix(".mmd")
    if trace_path.resolve() in {svg.resolve(), mermaid.resolve()}:
        raise ValueError("Output must not overwrite the input trace")
    svg.parent.mkdir(parents=True, exist_ok=True)
    svg.write_text(to_svg(nodes, complete), encoding="utf-8")
    mermaid.write_text(to_mermaid(nodes, complete), encoding="utf-8")
    return svg, mermaid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="One run in JSONL format.")
    parser.add_argument(
        "--output", type=Path, help="Output stem (default: trace stem)."
    )
    args = parser.parse_args(argv)
    try:
        paths = export_trace(args.trace, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot export trace: {exc}\n")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
