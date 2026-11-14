import { useEffect, useId, useMemo, useRef, useState } from "react";
import mermaid from "mermaid";

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "neutral",
});

export type GraphShape = {
  nodes: { id: string }[];
  edges: { source: string; target: string; conditional?: boolean }[];
};

export type GraphMapProps = {
  graph: GraphShape;
  /** How many times each node ran in this conversation turn(s). */
  visits: Record<string, number>;
  /** How many times each transition was taken, keyed "source->target". */
  transitions: Record<string, number>;
  /** Node running right now while streaming; null when idle. */
  current: string | null;
};

function sanitizeNodeId(raw: string, used: Set<string>): string {
  const cleaned = raw.replace(/^_+|_+$/g, "_").replace(/[^a-zA-Z0-9_]/g, "_");
  const base = raw.startsWith("_") ? `n${cleaned}` : `n_${cleaned}`;
  let candidate = base;
  let suffix = 2;
  while (used.has(candidate)) {
    candidate = `${base}_${suffix++}`;
  }
  used.add(candidate);
  return candidate;
}

function escapeLabel(text: string): string {
  return text.replace(/"/g, "'");
}

export function toMermaid({ graph, visits, transitions, current }: GraphMapProps): string {
  const lines: string[] = ["flowchart TD"];
  const idMap = new Map<string, string>();
  const usedIds = new Set<string>();

  const ensureNode = (id: string): string => {
    const existing = idMap.get(id);
    if (existing) return existing;
    const safeId = sanitizeNodeId(id, usedIds);
    idMap.set(id, safeId);
    const count = visits[id] ?? 0;
    const label = escapeLabel(count > 0 ? `${id} ×${count}` : id);
    const isTerminal = id === "__start__" || id === "__end__";
    lines.push(isTerminal ? `  ${safeId}(["${label}"])` : `  ${safeId}["${label}"]`);
    return safeId;
  };

  for (const node of graph.nodes) {
    ensureNode(node.id);
  }

  const takenEdgeIndices: number[] = [];
  graph.edges.forEach((edge, index) => {
    const src = ensureNode(edge.source);
    const dst = ensureNode(edge.target);
    const arrow = edge.conditional ? "-.->" : "-->";
    const count = transitions[`${edge.source}->${edge.target}`] ?? 0;
    if (count > 0) {
      lines.push(`  ${src} ${arrow}|"${count}"| ${dst}`);
      takenEdgeIndices.push(index);
    } else {
      lines.push(`  ${src} ${arrow} ${dst}`);
    }
  });

  lines.push("  classDef unvisited fill:#f1f5f9,stroke:#cbd5e1,color:#475569");
  lines.push("  classDef visited fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a");
  lines.push("  classDef current fill:#fef3c7,stroke:#d97706,stroke-width:3px,color:#78350f");

  const unvisited: string[] = [];
  const visited: string[] = [];
  const active: string[] = [];

  for (const [rawId, safeId] of idMap.entries()) {
    if (rawId === current) {
      active.push(safeId);
    } else if ((visits[rawId] ?? 0) > 0) {
      visited.push(safeId);
    } else {
      unvisited.push(safeId);
    }
  }

  if (unvisited.length > 0) lines.push(`  class ${unvisited.join(",")} unvisited`);
  if (visited.length > 0) lines.push(`  class ${visited.join(",")} visited`);
  if (active.length > 0) lines.push(`  class ${active.join(",")} current`);

  for (const idx of takenEdgeIndices) {
    lines.push(`  linkStyle ${idx} stroke:#2563eb,stroke-width:3px`);
  }

  return lines.join("\n");
}

export function GraphMap({ graph, visits, transitions, current }: GraphMapProps) {
  const reactId = useId();
  const renderId = useMemo(
    () => `graph_${reactId.replace(/[^a-zA-Z0-9_-]/g, "_")}`,
    [reactId],
  );
  const diagram = useMemo(
    () => toMermaid({ graph, visits, transitions, current }),
    [graph, visits, transitions, current],
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function renderDiagram() {
      if (graph.nodes.length === 0) {
        if (containerRef.current) containerRef.current.innerHTML = "";
        setUnavailable(true);
        return;
      }

      try {
        setUnavailable(false);
        const { svg } = await mermaid.render(renderId, diagram);
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg;
        }
      } catch {
        if (!cancelled) {
          if (containerRef.current) containerRef.current.innerHTML = "";
          setUnavailable(true);
        }
      }
    }

    void renderDiagram();
    return () => {
      cancelled = true;
    };
  }, [diagram, renderId, graph.nodes.length]);

  return (
    <div className="w-full overflow-x-auto">
      <div
        ref={containerRef}
        className={
          unavailable
            ? "hidden"
            : "flex justify-center [&_svg]:max-w-full [&_svg]:h-auto"
        }
      />
      {unavailable && (
        <p className="py-4 text-center text-xs text-slate-500">Graph unavailable</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded border border-slate-300 bg-slate-100" />
          not visited
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded border border-blue-500 bg-blue-100" />
          visited (×count)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded border-2 border-amber-600 bg-amber-100" />
          current node
        </span>
      </div>
    </div>
  );
}
