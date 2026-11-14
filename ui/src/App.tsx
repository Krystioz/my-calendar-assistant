import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Thread } from "@langchain/langgraph-sdk";
import type { GraphShape } from "./GraphMap";
import { Message } from "./Message";
import {
  ASSISTANT_ID,
  buildTurnsFromState,
  client,
  extractInterrupt,
  extractNodeSequenceFromHistory,
  extractTaskError,
  extractTurnTotals,
  fetchGraphShape,
  getThreadSettings,
  type AgentInterrupt,
  type AgentStateValues,
  type CalendarBackend,
  type TurnTotals,
} from "./api";

// Long conversations write many checkpoints; 100 cut the node path short.
const HISTORY_LIMIT = 1000;

export function App() {
  const [threads, setThreads] = useState<Thread<AgentStateValues>[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphShape | null>(null);

  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [calendar, setCalendar] = useState<CalendarBackend>("local");
  const [today, setToday] = useState<string>("");

  const [stateValues, setStateValues] = useState<AgentStateValues | null>(null);
  const [interrupt, setInterrupt] = useState<AgentInterrupt | null>(null);
  const [nodeSequence, setNodeSequence] = useState<string[]>([]);
  const [turnTotals, setTurnTotals] = useState<TurnTotals>({});
  const [loadingThread, setLoadingThread] = useState(false);

  const [isStreaming, setIsStreaming] = useState(false);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const loadSeqRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  async function refreshThreads() {
    try {
      const list = await client.threads.search<AgentStateValues>({
        limit: 50,
        sortBy: "updated_at",
        sortOrder: "desc",
      });
      setThreads(list);
      setThreadsError(null);
    } catch (err) {
      setThreadsError(
        err instanceof Error ? err.message : "Failed to load threads",
      );
    } finally {
      setThreadsLoading(false);
    }
  }

  useEffect(() => {
    void refreshThreads();
    void fetchGraphShape()
      .then(setGraph)
      .catch(() => {
        // Graph panel stays hidden if graph metadata cannot be fetched
      });
  }, []);

  const turns = buildTurnsFromState(
    stateValues,
    interrupt,
    nodeSequence,
    turnTotals,
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, activeNode, interrupt, runError]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  async function selectThread(thread: Thread<AgentStateValues>) {
    if (isStreaming) return;
    const seq = ++loadSeqRef.current;
    const settings = getThreadSettings(thread);
    setSelectedThreadId(thread.thread_id);
    setCalendar(settings.calendar);
    setToday(settings.today);
    setSidebarOpen(false);
    setLoadingThread(true);
    setRunError(null);
    setTurnTotals({});

    try {
      const [st, hist] = await Promise.all([
        client.threads.getState<AgentStateValues>(thread.thread_id),
        client.threads.getHistory<AgentStateValues>(thread.thread_id, {
          limit: HISTORY_LIMIT,
        }),
      ]);
      if (loadSeqRef.current !== seq) return;
      setStateValues(st.values ?? null);
      setInterrupt(extractInterrupt(st));
      setNodeSequence(extractNodeSequenceFromHistory(hist));
      setTurnTotals(extractTurnTotals(hist));
      const taskErr = extractTaskError(st);
      const threadErr =
        typeof thread.error === "string"
          ? thread.error
          : thread.error
            ? JSON.stringify(thread.error)
            : null;
      setRunError(taskErr ?? threadErr);
    } catch (err) {
      if (loadSeqRef.current !== seq) return;
      setRunError(
        err instanceof Error ? err.message : "Failed to load conversation",
      );
    } finally {
      if (loadSeqRef.current === seq) {
        setLoadingThread(false);
      }
    }
  }

  function handleNewChat() {
    if (isStreaming) return;
    loadSeqRef.current += 1;
    setSelectedThreadId(null);
    setStateValues(null);
    setInterrupt(null);
    setNodeSequence([]);
    setTurnTotals({});
    setRunError(null);
    setActiveNode(null);
    setSidebarOpen(false);
    textareaRef.current?.focus();
  }

  async function streamRun(
    threadId: string,
    runCalendar: CalendarBackend,
    runToday: string,
    payload: { input: { goal: string } } | { command: { resume: unknown } },
  ) {
    setIsStreaming(true);
    setActiveNode(null);
    setRunError(null);
    // A resume re-runs the paused node from its start; history already lists
    // it once, so its first update must not add it a second time.
    let skipResumedNode = "command" in payload;

    try {
      const stream = client.runs.stream(threadId, ASSISTANT_ID, {
        ...payload,
        context: {
          calendar: runCalendar,
          ...(runToday ? { today: runToday } : {}),
        },
        streamMode: ["updates", "values"],
      });

      for await (const chunk of stream) {
        if (chunk.event === "updates" && chunk.data && typeof chunk.data === "object") {
          const entries = Object.entries(chunk.data as Record<string, unknown>);
          for (const [nodeName, nodeUpdate] of entries) {
            if (nodeName === "__interrupt__") {
              setActiveNode(null);
              continue;
            }
            if (!nodeName.startsWith("__")) {
              const resumed = skipResumedNode;
              skipResumedNode = false;
              setNodeSequence((prev) =>
                resumed && prev[prev.length - 1] === nodeName ? prev : [...prev, nodeName],
              );
              // An update arrives when a node has finished; the node running now
              // is the one it routed to.
              const next = (nodeUpdate as { next_node?: unknown } | null)?.next_node;
              setActiveNode(typeof next === "string" && next !== "end" ? next : null);
            }
          }
        } else if (
          chunk.event === "values" &&
          chunk.data &&
          typeof chunk.data === "object"
        ) {
          setStateValues(chunk.data as AgentStateValues);
        } else if (chunk.event === "error") {
          const errData = chunk.data as Record<string, unknown> | undefined;
          const msg =
            (typeof errData?.message === "string" && errData.message) ||
            (typeof errData?.error === "string" && errData.error) ||
            JSON.stringify(chunk.data);
          setRunError(msg);
        }
      }
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Run failed");
    } finally {
      setActiveNode(null);
      setIsStreaming(false);
      try {
        const [freshState, hist] = await Promise.all([
          client.threads.getState<AgentStateValues>(threadId),
          client.threads.getHistory<AgentStateValues>(threadId, { limit: HISTORY_LIMIT }),
        ]);
        if (freshState.values && Object.keys(freshState.values).length > 0) {
          setStateValues(freshState.values);
        }
        setInterrupt(extractInterrupt(freshState));
        const histSeq = extractNodeSequenceFromHistory(hist);
        if (histSeq.length > 0) {
          setNodeSequence(histSeq);
        }
        setTurnTotals(extractTurnTotals(hist));
        const taskErr = extractTaskError(freshState);
        if (taskErr) {
          setRunError(taskErr);
        }
      } catch (err) {
        setRunError((prev) =>
          prev ?? (err instanceof Error ? err.message : "Failed to fetch final state"),
        );
      }
      void refreshThreads();
    }
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || isStreaming || loadingThread || interrupt?.kind === "confirm") {
      return;
    }

    setInput("");

    if (selectedThreadId !== null && interrupt?.kind === "clarify") {
      const targetThreadId = selectedThreadId;
      setInterrupt(null);
      setStateValues((prev) => ({
        ...(prev ?? {}),
        status: null,
        messages: [
          ...(prev?.messages ?? []),
          { role: "user", content: text },
        ],
      }));
      await streamRun(
        targetThreadId,
        calendar,
        today,
        { command: { resume: text } },
      );
      return;
    }

    // New thread (either from New chat screen or after a finished thread)
    const runCalendar = calendar;
    const runToday = today;
    setStateValues({
      goal: text,
      messages: [{ role: "user", content: text }],
    });
    setInterrupt(null);
    setNodeSequence([]);
    setTurnTotals({});
    setRunError(null);

    try {
      const created = await client.threads.create({
        metadata: {
          title: text,
          calendar: runCalendar,
          today: runToday,
        },
      });
      setSelectedThreadId(created.thread_id);
      setThreads((prev) => [
        created as unknown as Thread<AgentStateValues>,
        ...prev,
      ]);
      await streamRun(
        created.thread_id,
        runCalendar,
        runToday,
        { input: { goal: text } },
      );
    } catch (err) {
      setRunError(
        err instanceof Error ? err.message : "Failed to start conversation",
      );
      setIsStreaming(false);
    }
  }

  async function handleConfirm(approved: boolean) {
    if (!selectedThreadId || isStreaming || interrupt?.kind !== "confirm") {
      return;
    }
    const targetThreadId = selectedThreadId;
    setInterrupt(null);
    await streamRun(
      targetThreadId,
      calendar,
      today,
      { command: { resume: approved } },
    );
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void handleSend();
    }
  }

  const isWaitingClarify = interrupt?.kind === "clarify";
  const isWaitingConfirm = interrupt?.kind === "confirm";
  const hasEnded =
    selectedThreadId !== null &&
    !isStreaming &&
    !loadingThread &&
    !isWaitingClarify &&
    !isWaitingConfirm;

  const composerDisabled = isStreaming || loadingThread || isWaitingConfirm;
  const composerPlaceholder = isWaitingConfirm
    ? "Choose Yes or No above to continue..."
    : isWaitingClarify
      ? "Reply to the clarifying question..."
      : hasEnded
        ? "Ask a new question (starts a new conversation)"
        : "Ask about your calendar or documents...";

  const selectedThread = threads.find((t) => t.thread_id === selectedThreadId);
  const headerTitle = selectedThread
    ? getThreadSettings(selectedThread).title
    : stateValues?.goal || "New chat";

  return (
    <div className="flex h-full bg-white text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Left sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-64 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 transition-transform duration-150 dark:border-zinc-800 dark:bg-zinc-900 md:static md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="p-3">
          <button
            type="button"
            disabled={isStreaming}
            onClick={handleNewChat}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm font-medium text-zinc-800 shadow-xs hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700"
          >
            <span>+ New chat</span>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-4">
          {threadsLoading ? (
            <div className="px-3 py-2 text-xs text-zinc-400">
              Loading conversations...
            </div>
          ) : threadsError ? (
            <div className="px-3 py-2 text-xs text-red-600 dark:text-red-400">
              {threadsError}
            </div>
          ) : threads.length === 0 ? (
            <div className="px-3 py-2 text-xs text-zinc-400">
              No conversations yet
            </div>
          ) : (
            <ul className="space-y-0.5">
              {threads.map((thread) => {
                const settings = getThreadSettings(thread);
                const active = thread.thread_id === selectedThreadId;
                return (
                  <li key={thread.thread_id}>
                    <button
                      type="button"
                      disabled={isStreaming}
                      onClick={() => void selectThread(thread)}
                      title={settings.title}
                      className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors disabled:opacity-60 ${
                        active
                          ? "bg-zinc-200/80 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50"
                          : "text-zinc-700 hover:bg-zinc-200/50 dark:text-zinc-300 dark:hover:bg-zinc-800/60"
                      }`}
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {settings.title}
                      </span>
                      {thread.status === "interrupted" && (
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"
                          title="Waiting for input"
                        />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </aside>

      {/* Main chat area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top header */}
        <header className="flex h-13 shrink-0 items-center justify-between gap-3 border-b border-zinc-200 px-4 dark:border-zinc-800">
          <div className="flex min-w-0 items-center gap-2.5">
            <button
              type="button"
              onClick={() => setSidebarOpen((prev) => !prev)}
              className="rounded-md p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800 md:hidden"
              aria-label="Toggle sidebar"
            >
              ☰
            </button>
            <h1 className="truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">
              {headerTitle}
            </h1>
          </div>

          {selectedThreadId !== null && (
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="rounded-full border border-zinc-200 bg-zinc-50 px-2.5 py-0.5 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                Calendar: <strong className="font-medium">{calendar}</strong>
              </span>
              <span className="rounded-full border border-zinc-200 bg-zinc-50 px-2.5 py-0.5 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                Today:{" "}
                <strong className="font-medium">
                  {today || "real date"}
                </strong>
              </span>
            </div>
          )}
        </header>

        {/* Scrollable chat column */}
        <main className="flex-1 overflow-y-auto px-4 py-6">
          <div className="mx-auto max-w-3xl">
            {selectedThreadId === null ? (
              <div className="flex min-h-[55vh] flex-col items-center justify-center text-center">
                <h2 className="mb-2 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
                  Calendar Assistant
                </h2>
                <p className="mb-6 max-w-md text-sm text-zinc-500 dark:text-zinc-400">
                  Ask about events, availability, conflicts, or study documents.
                </p>

                <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-zinc-50/70 p-4 text-left dark:border-zinc-800 dark:bg-zinc-900/60">
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-300">
                        Calendar
                      </label>
                      <div className="grid grid-cols-2 gap-1.5 rounded-lg border border-zinc-200 bg-white p-1 dark:border-zinc-700 dark:bg-zinc-950">
                        {(["local", "google"] as const).map((option) => (
                          <button
                            key={option}
                            type="button"
                            onClick={() => setCalendar(option)}
                            className={`rounded-md px-2.5 py-1.5 text-xs font-medium capitalize transition-colors ${
                              calendar === option
                                ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                                : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                            }`}
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div>
                      <div className="mb-1.5 flex items-center justify-between">
                        <label
                          htmlFor="today-input"
                          className="text-xs font-medium text-zinc-600 dark:text-zinc-300"
                        >
                          Today (optional)
                        </label>
                        {today && (
                          <button
                            type="button"
                            onClick={() => setToday("")}
                            className="text-[11px] text-blue-600 hover:underline dark:text-blue-400"
                          >
                            Use real date
                          </button>
                        )}
                      </div>
                      <input
                        id="today-input"
                        type="date"
                        value={today}
                        onChange={(e) => setToday(e.target.value)}
                        className="w-full rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs text-zinc-900 focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                      />
                    </div>
                  </div>
                </div>
              </div>
            ) : loadingThread ? (
              <div className="py-12 text-center text-sm text-zinc-400">
                Loading conversation...
              </div>
            ) : (
              <div className="space-y-8">
                {turns.map((turn, idx) => {
                  const isLast = idx === turns.length - 1;
                  return (
                    <Message
                      key={turn.id}
                      turn={turn}
                      graph={graph}
                      isStreamingTurn={isLast && isStreaming}
                      activeNode={isLast ? activeNode : null}
                      confirmInterrupt={
                        isLast && interrupt?.kind === "confirm" ? interrupt : null
                      }
                      onConfirm={(approved) => void handleConfirm(approved)}
                      disableActions={isStreaming}
                      runError={isLast ? runError : null}
                    />
                  );
                })}
                {turns.length === 0 && runError && (
                  <div className="rounded-xl border border-red-200 bg-red-50/80 px-4 py-3 text-sm text-red-900 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-100">
                    {runError}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </main>

        {/* Pinned bottom composer */}
        <footer className="shrink-0 border-t border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
          <div className="mx-auto max-w-3xl">
            <div className="flex items-end gap-2 rounded-2xl border border-zinc-300 bg-white px-3.5 py-2 shadow-xs focus-within:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:focus-within:border-zinc-500">
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                disabled={composerDisabled}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={composerPlaceholder}
                className="max-h-40 flex-1 resize-none bg-transparent py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none disabled:opacity-50 dark:text-zinc-100 dark:placeholder:text-zinc-500"
              />
              <button
                type="button"
                disabled={composerDisabled || !input.trim()}
                onClick={() => void handleSend()}
                className="shrink-0 rounded-xl bg-zinc-900 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                {isStreaming ? "Running..." : "Send"}
              </button>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
