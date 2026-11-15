import { useState } from "react";
import Markdown from "react-markdown";
import { GraphMap, type GraphShape } from "./GraphMap";
import { Steps } from "./Steps";
import { CitationChip } from "./CitationChip";
import { RecordCard } from "./cards";
import {
  computeGraphStats,
  type AgentLimits,
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

/** "n / max" with a thin bar: amber above 75%, red at the limit. */
function BudgetMeter({ label, used, max }: { label: string; used: number; max: number }) {
  const ratio = max > 0 ? used / max : 0;
  const bar = ratio >= 1 ? "bg-red-500" : ratio > 0.75 ? "bg-amber-500" : "bg-zinc-400 dark:bg-zinc-500";
  return (
    <span className="inline-flex flex-col gap-0.5 align-middle">
      <span>{label}</span>
      <span className="h-0.5 w-full min-w-12 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
        <span className={`block h-full ${bar}`} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </span>
    </span>
  );
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
  limits?: AgentLimits | null;
  clarifyOptions?: string[];
  onPickOption?: (text: string) => void;
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
  limits,
  clarifyOptions,
  onPickOption,
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
  const isDelete = confirmInterrupt?.tool?.startsWith("delete_") ?? false;
  const hasAnswer = turn.assistantAnswer !== null && turn.assistantAnswer !== "";

  return (
    <div className="space-y-4">
      {/* User bubble (right) */}
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-zinc-100 px-4 py-2.5 text-sm leading-relaxed text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
          {turn.userImage && (
            <img
              src={turn.userImage}
              alt="Attached image"
              className="mb-2 max-h-60 rounded-lg"
            />
          )}
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

        {/* One-click answers to a clarify question */}
        {turn.status === "clarify" && onPickOption && clarifyOptions && clarifyOptions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {clarifyOptions.map((opt) => (
              <button
                key={opt}
                type="button"
                disabled={disableActions}
                onClick={() => onPickOption(opt)}
                className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-xs text-sky-800 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-200 dark:hover:bg-sky-900/40"
              >
                {opt}
              </button>
            ))}
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
            {confirmInterrupt.target && (
              <div className="mb-3">
                <RecordCard
                  record={confirmInterrupt.target}
                  tag={
                    confirmInterrupt.tool?.startsWith("delete_")
                      ? { label: "will be deleted", tone: "red" }
                      : undefined
                  }
                />
              </div>
            )}
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={disableActions}
                onClick={() => onConfirm(true)}
                className="rounded-lg bg-zinc-900 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                {isDelete ? "Delete" : "Yes"}
              </button>
              <button
                type="button"
                disabled={disableActions}
                onClick={() => onConfirm(false)}
                className="rounded-lg border border-zinc-300 bg-white px-3.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                {isDelete ? "Keep" : "No"}
              </button>
            </div>
          </div>
        )}

        {/* Citations chips */}
        {turn.citations.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            {turn.citations.map((cid) => (
              <CitationChip key={cid} id={cid} />
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
            {limits ? (
              <>
                {turn.steps !== null && (
                  <>
                    {" · "}
                    <BudgetMeter
                      label={`${turn.steps} / ${limits.max_steps} steps`}
                      used={turn.steps}
                      max={limits.max_steps}
                    />
                  </>
                )}
                {turn.costUsd !== null && (
                  <>
                    {" · "}
                    <BudgetMeter
                      label={`${formatCost(turn.costUsd)} / ${limits.max_cost_usd}`}
                      used={turn.costUsd}
                      max={limits.max_cost_usd}
                    />
                  </>
                )}
              </>
            ) : (
              <>
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
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
