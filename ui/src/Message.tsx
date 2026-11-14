import { useState } from "react";
import Markdown from "react-markdown";
import { GraphMap, type GraphShape } from "./GraphMap";
import { Steps } from "./Steps";
import {
  computeGraphStats,
  type AgentStatus,
  type ConfirmInterrupt,
  type ConversationTurn,
} from "./api";

function formatCost(cost: number): string {
  if (cost > 0 && cost < 0.0001) {
    return `$${cost.toFixed(5)}`;
  }
  return `$${cost.toFixed(4)}`;
}

function statusContainerClass(status: AgentStatus | null): string {
  if (status === "error") {
    return "rounded-xl border border-red-200 bg-red-50/70 px-4 py-3 text-red-950 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-100";
  }
  if (
    status === "not_found" ||
    status === "step_limit" ||
    status === "cost_limit"
  ) {
    return "rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100";
  }
  return "";
}

function statusBadgeClass(status: AgentStatus): string {
  switch (status) {
    case "success":
      return "text-emerald-700 dark:text-emerald-400";
    case "clarify":
      return "text-sky-700 dark:text-sky-400";
    case "not_found":
    case "step_limit":
    case "cost_limit":
      return "text-amber-700 dark:text-amber-400";
    case "error":
      return "text-red-700 dark:text-red-400";
  }
}

type MessageProps = {
  turn: ConversationTurn;
  graph: GraphShape | null;
  isStreamingTurn: boolean;
  activeNode: string | null;
  confirmInterrupt: ConfirmInterrupt | null;
  onConfirm: (approved: boolean) => void;
  disableActions: boolean;
  runError?: string | null;
};

export function Message({
  turn,
  graph,
  isStreamingTurn,
  activeNode,
  confirmInterrupt,
  onConfirm,
  disableActions,
  runError,
}: MessageProps) {
  const [graphOpen, setGraphOpen] = useState(false);

  // __start__/__end__ never appear in the stream: add them so the map shows
  // the entry and the exit. A clarify or confirm pause has not ended the run.
  const seq = turn.nodeSequence;
  const ended =
    !isStreamingTurn && turn.status !== null && turn.status !== "clarify" && !confirmInterrupt;
  const { visits, transitions } = computeGraphStats([
    ...(seq[0] === "start" ? ["__start__"] : []),
    ...seq,
    ...(ended && seq.length > 0 ? ["__end__"] : []),
  ]);
  const showFooter = turn.status !== null;
  const hasAnswer = turn.assistantAnswer !== null && turn.assistantAnswer !== "";

  return (
    <div className="space-y-4">
      {/* User bubble (right) */}
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-zinc-100 px-4 py-2.5 text-sm leading-relaxed text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
          {turn.userMessage}
        </div>
      </div>

      {/* Assistant turn (left) */}
      <div className="space-y-2 text-sm leading-relaxed">
        {/* Live node progress while streaming */}
        {isStreamingTurn && (
          <div className="rounded-lg border border-zinc-200 bg-zinc-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900/60">
            <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-zinc-600 dark:text-zinc-300">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
              <span>
                {activeNode ? `Running: ${activeNode}` : "Starting run..."}
              </span>
            </div>
            {turn.nodeSequence.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {[...turn.nodeSequence, ...(activeNode ? [activeNode] : [])].map((node, i, all) => {
                  // The finished nodes, then the one running now.
                  const isCurrent = activeNode !== null && i === all.length - 1;
                  return (
                    <span
                      key={`${node}-${i}`}
                      className="inline-flex items-center gap-1"
                    >
                      <span
                        className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                          isCurrent
                            ? "bg-blue-100 font-semibold text-blue-800 dark:bg-blue-900/60 dark:text-blue-200"
                            : "bg-zinc-200/80 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                        }`}
                      >
                        {node}
                      </span>
                      {i < all.length - 1 && (
                        <span className="text-xs text-zinc-400">→</span>
                      )}
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Answer body */}
        {hasAnswer && (
          <div className={statusContainerClass(turn.status)}>
            {turn.status &&
              turn.status !== "success" &&
              turn.status !== "clarify" && (
                <div
                  className={`mb-1 text-xs font-semibold uppercase tracking-wide ${statusBadgeClass(turn.status)}`}
                >
                  {turn.status.replace("_", " ")}
                </div>
              )}
            <div className="markdown-body text-sm leading-relaxed">
              <Markdown>{turn.assistantAnswer}</Markdown>
            </div>
          </div>
        )}

        {/* Unhandled server / run error */}
        {runError && (
          <div className="rounded-xl border border-red-200 bg-red-50/80 px-4 py-3 text-sm text-red-900 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-100">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-red-700 dark:text-red-400">
              Error
            </div>
            <div className="whitespace-pre-wrap">{runError}</div>
          </div>
        )}

        {/* Confirmation interrupt prompt */}
        {confirmInterrupt && (
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3.5 dark:border-amber-900/60 dark:bg-amber-950/30">
            <p className="mb-3 text-sm font-medium text-zinc-900 dark:text-zinc-100">
              {confirmInterrupt.question}
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={disableActions}
                onClick={() => onConfirm(true)}
                className="rounded-lg bg-zinc-900 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                Yes
              </button>
              <button
                type="button"
                disabled={disableActions}
                onClick={() => onConfirm(false)}
                className="rounded-lg border border-zinc-300 bg-white px-3.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                No
              </button>
            </div>
          </div>
        )}

        {/* Citations chips */}
        {turn.citations.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            {turn.citations.map((cid) => (
              <span
                key={cid}
                className="rounded-full border border-zinc-200 bg-zinc-50 px-2.5 py-0.5 font-mono text-[11px] text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
              >
                {cid}
              </span>
            ))}
          </div>
        )}

        {/* Collapsed Steps block */}
        <Steps nodeSequence={turn.nodeSequence} toolCalls={turn.toolCalls} />

        {/* Collapsible Graph panel */}
        {graph && (isStreamingTurn || turn.status !== null || confirmInterrupt !== null || turn.nodeSequence.length > 0) && (
          <div className="rounded-lg border border-zinc-200 bg-zinc-50/70 dark:border-zinc-800 dark:bg-zinc-900/50">
            <button
              type="button"
              onClick={() => setGraphOpen((prev) => !prev)}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
            >
              <span>Graph</span>
              <span className="text-[11px]">{graphOpen ? "▲" : "▼"}</span>
            </button>
            {graphOpen && (
              <div className="border-t border-zinc-200 p-3 dark:border-zinc-800">
                <GraphMap
                  graph={graph}
                  visits={visits}
                  transitions={transitions}
                  current={isStreamingTurn ? activeNode : null}
                />
              </div>
            )}
          </div>
        )}

        {/* Footer: status · N steps · $cost */}
        {showFooter && turn.status && (
          <div className="pt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
            <span className={`font-medium ${statusBadgeClass(turn.status)}`}>
              {turn.status}
            </span>
            {turn.steps !== null && (
              <span>
                {" · "}
                {turn.steps} {turn.steps === 1 ? "step" : "steps"}
              </span>
            )}
            {turn.costUsd !== null && (
              <span>
                {" · "}
                {formatCost(turn.costUsd)}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
