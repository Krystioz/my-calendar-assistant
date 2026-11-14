import { useState } from "react";
import type { ToolCallStep } from "./api";

function formatJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function ToolResultView({ raw }: { raw: string | null }) {
  const [expanded, setExpanded] = useState(false);

  if (raw === null) {
    return (
      <div className="text-xs italic text-zinc-400 dark:text-zinc-500">
        Pending result...
      </div>
    );
  }

  const formatted = formatJson(raw);
  const needsTruncate = formatted.length > 300;
  const display =
    needsTruncate && !expanded ? `${formatted.slice(0, 300)}...` : formatted;

  return (
    <div>
      <pre className="overflow-x-auto rounded bg-zinc-100 p-2 text-xs text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
        {display}
      </pre>
      {needsTruncate && (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="mt-1 text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          {expanded ? "Show less" : `Show full (${formatted.length} chars)`}
        </button>
      )}
    </div>
  );
}

type StepsProps = {
  nodeSequence: string[];
  toolCalls: ToolCallStep[];
  defaultOpen?: boolean;
};

export function Steps({ nodeSequence, toolCalls, defaultOpen = false }: StepsProps) {
  const [open, setOpen] = useState(defaultOpen);
  // Several tool calls can share one model step, and a text reply is a step too.
  const modelSteps = new Set(toolCalls.map((tc) => tc.step)).size;

  if (nodeSequence.length === 0 && toolCalls.length === 0) {
    return null;
  }

  return (
    <div className="mt-2 rounded-lg border border-zinc-200 bg-zinc-50/70 dark:border-zinc-800 dark:bg-zinc-900/50">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
      >
        <span>
          Steps
          <span className="ml-2 font-normal text-zinc-400 dark:text-zinc-500">
            {nodeSequence.length > 0 ? `${nodeSequence.length} nodes` : ""}
            {nodeSequence.length > 0 && modelSteps > 0 ? " · " : ""}
            {modelSteps > 0
              ? `${modelSteps} model ${modelSteps === 1 ? "step" : "steps"}`
              : ""}
          </span>
        </span>
        <span className="text-[11px]">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
          {nodeSequence.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              {nodeSequence.map((node, i) => (
                <span key={`${node}-${i}`} className="inline-flex items-center gap-1">
                  <span className="rounded bg-zinc-200/80 px-1.5 py-0.5 font-mono text-[11px] text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                    {node}
                  </span>
                  {i < nodeSequence.length - 1 && (
                    <span className="text-xs text-zinc-400">→</span>
                  )}
                </span>
              ))}
            </div>
          )}

          {toolCalls.length > 0 && (
            <div className="space-y-2.5">
              {toolCalls.map((tc) => (
                <div
                  key={tc.id}
                  className="rounded-md border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-950"
                >
                  <div className="mb-1.5 flex items-center gap-2 text-xs">
                    <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                      Step {tc.step}
                    </span>
                    <span className="font-mono font-semibold text-zinc-800 dark:text-zinc-100">
                      {tc.name}
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    <div>
                      <div className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
                        Arguments
                      </div>
                      <pre className="overflow-x-auto rounded bg-zinc-100 p-2 text-xs text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
                        {formatJson(tc.argumentsRaw)}
                      </pre>
                    </div>
                    <div>
                      <div className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-zinc-400">
                        Result
                      </div>
                      <ToolResultView raw={tc.resultRaw} />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
