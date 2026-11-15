import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Thread } from "@langchain/langgraph-sdk";
import type { GraphShape } from "./GraphMap";
import { Message } from "./Message";
import { CalendarPanel } from "./CalendarPanel";
import {
  ASSISTANT_ID,
  buildTurnsFromState,
  client,
  extractChangedIds,
  extractInterrupt,
  extractNodeSequenceFromHistory,
  extractTaskError,
  extractTurnTotals,
  fetchGraphShape,
  fetchLimits,
  getThreadSettings,
  type AgentInterrupt,
  type AgentLimits,
  type AgentStateValues,
  type CalendarBackend,
  type TurnTotals,
} from "./api";

// Long conversations write many checkpoints; 100 cut the node path short.
const HISTORY_LIMIT = 1000;

const PANEL_KEY = "calendarPanelOpen";

const EXAMPLE_PROMPTS = [
  "Kiedy mam najbliższe laboratorium?",
  "Czy jestem wolny jutro między 14 a 16?",
  "Dodaj spotkanie z promotorem w piątek o 10 z przypomnieniem godzinę wcześniej",
  "Jakie zadania mam na dziś?",
  "Dodaj zadanie: kupić bilet do kina",
  "Co grają w kinie w ten weekend?",
];

function initialPanelOpen(): boolean {
  try {
    const stored = localStorage.getItem(PANEL_KEY);
    if (stored !== null) return stored === "1";
  } catch {
    // Storage blocked: fall back to the screen width
  }
  return window.matchMedia("(min-width: 1024px)").matches;
}

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
  const [panelOpen, setPanelOpen] = useState(initialPanelOpen);
  const [refreshKey, setRefreshKey] = useState(0);
  const [limits, setLimits] = useState<AgentLimits | null>(null);
  const [threadQuery, setThreadQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

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
    void fetchLimits()
      .then(setLimits)
      .catch(() => {
        // Limits are optional: Message renders without them
      });
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(PANEL_KEY, panelOpen ? "1" : "0");
    } catch {
      // Storage blocked: the panel state is just not remembered
    }
  }, [panelOpen]);

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
    setRefreshKey((k) => k + 1);

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
      setRefreshKey((k) => k + 1);
      void refreshThreads();
    }
  }

  // `override` sends a picked clarify option and leaves the typed draft alone.
  async function handleSend(override?: string) {
    const text = (override ?? input).trim();
    if (!text || isStreaming || loadingThread || interrupt?.kind === "confirm") {
      return;
    }

    if (override === undefined) setInput("");

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

  function startRename(thread: Thread<AgentStateValues>) {
    if (isStreaming) return;
    setEditingId(thread.thread_id);
    setEditTitle(getThreadSettings(thread).title);
  }

  async function saveRename(thread: Thread<AgentStateValues>) {
    const title = editTitle.trim();
    setEditingId(null);
    if (!title || title === getThreadSettings(thread).title) return;
    try {
      const meta = (thread.metadata ?? {}) as Record<string, unknown>;
      await client.threads.update(thread.thread_id, { metadata: { ...meta, title } });
      await refreshThreads();
    } catch (err) {
      setThreadsError(err instanceof Error ? err.message : "Failed to rename conversation");
    }
  }

  async function deleteThread(threadId: string) {
    if (isStreaming || !window.confirm("Delete this conversation?")) return;
    try {
      await client.threads.delete(threadId);
      if (threadId === selectedThreadId) handleNewChat();
      setThreads((prev) => prev.filter((t) => t.thread_id !== threadId));
    } catch (err) {
      setThreadsError(err instanceof Error ? err.message : "Failed to delete conversation");
    }
  }

  function pickExample(prompt: string) {
    setInput(prompt);
    textareaRef.current?.focus();
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

  const changedIds = extractChangedIds(stateValues);
  const query = threadQuery.trim().toLowerCase();
  const visibleThreads = query
    ? threads.filter((t) => getThreadSettings(t).title.toLowerCase().includes(query))
    : threads;

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
          <input
            type="search"
            value={threadQuery}
            onChange={(e) => setThreadQuery(e.target.value)}
            placeholder="Search conversations..."
            aria-label="Search conversations"
            className="mt-2 w-full rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />
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
          ) : visibleThreads.length === 0 ? (
            <div className="px-3 py-2 text-xs text-zinc-400">
              No matching conversations
            </div>
          ) : (
            <ul className="space-y-0.5">
              {visibleThreads.map((thread) => {
                const settings = getThreadSettings(thread);
                const active = thread.thread_id === selectedThreadId;
                const failed =
                  thread.status === "error" ||
                  (thread.values as AgentStateValues | undefined)?.status === "error";
                if (editingId === thread.thread_id) {
                  return (
                    <li key={thread.thread_id} className="px-1 py-1">
                      <input
                        autoFocus
                        value={editTitle}
                        aria-label="Conversation title"
                        onChange={(e) => setEditTitle(e.target.value)}
                        onBlur={() => void saveRename(thread)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.currentTarget.blur();
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        className="w-full rounded-md border border-zinc-400 bg-white px-2 py-1 text-sm text-zinc-900 focus:outline-none dark:border-zinc-500 dark:bg-zinc-950 dark:text-zinc-100"
                      />
                    </li>
                  );
                }
                return (
                  <li
                    key={thread.thread_id}
                    className={`group flex items-center rounded-lg transition-colors ${
                      active
                        ? "bg-zinc-200/80 dark:bg-zinc-800"
                        : "hover:bg-zinc-200/50 dark:hover:bg-zinc-800/60"
                    }`}
                  >
                    <button
                      type="button"
                      disabled={isStreaming}
                      onClick={() => void selectThread(thread)}
                      onDoubleClick={() => startRename(thread)}
                      title={settings.title}
                      className={`flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-sm disabled:opacity-60 ${
                        active
                          ? "font-medium text-zinc-900 dark:text-zinc-50"
                          : "text-zinc-700 dark:text-zinc-300"
                      }`}
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {settings.title}
                      </span>
                      <span className="shrink-0 rounded border border-zinc-200 px-1 text-[10px] font-normal text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                        {settings.calendar === "google" ? "google" : "local"}
                      </span>
                      {thread.status === "interrupted" ? (
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"
                          title="Waiting for input"
                        />
                      ) : failed ? (
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500"
                          title="Ended with an error"
                        />
                      ) : null}
                    </button>
                    <div className="flex shrink-0 items-center pr-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                      <button
                        type="button"
                        disabled={isStreaming}
                        onClick={() => startRename(thread)}
                        aria-label={`Rename conversation ${settings.title}`}
                        title="Rename"
                        className="rounded p-1 text-xs text-zinc-500 hover:bg-zinc-300/60 hover:text-zinc-900 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-700 dark:hover:text-zinc-100"
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        disabled={isStreaming}
                        onClick={() => void deleteThread(thread.thread_id)}
                        aria-label={`Delete conversation ${settings.title}`}
                        title="Delete"
                        className="rounded p-1 text-xs text-zinc-500 hover:bg-red-100 hover:text-red-700 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-red-950 dark:hover:text-red-300"
                      >
                        🗑
                      </button>
                    </div>
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

          <div className="flex shrink-0 items-center gap-1.5">
            {selectedThreadId !== null && (
              <>
              <span className="rounded-full border border-zinc-200 bg-zinc-50 px-2.5 py-0.5 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                Calendar: <strong className="font-medium">{calendar}</strong>
              </span>
              <span className="rounded-full border border-zinc-200 bg-zinc-50 px-2.5 py-0.5 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                Today:{" "}
                <strong className="font-medium">
                  {today || "real date"}
                </strong>
              </span>
              </>
            )}
            <button
              type="button"
              onClick={() => setPanelOpen((prev) => !prev)}
              aria-label={panelOpen ? "Hide calendar panel" : "Show calendar panel"}
              aria-pressed={panelOpen}
              title="Calendar panel"
              className={`rounded-md p-1.5 text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800 ${
                panelOpen ? "bg-zinc-100 dark:bg-zinc-800" : ""
              }`}
            >
              📅
            </button>
          </div>
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

                <div className="mt-4 grid w-full max-w-md grid-cols-1 gap-2 sm:grid-cols-2">
                  {EXAMPLE_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => pickExample(prompt)}
                      className="rounded-xl border border-zinc-200 bg-white px-3 py-2 text-left text-xs text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:bg-zinc-800"
                    >
                      {prompt}
                    </button>
                  ))}
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
                      limits={limits}
                      clarifyOptions={
                        isLast && interrupt?.kind === "clarify" ? interrupt.options : undefined
                      }
                      onPickOption={(text: string) => void handleSend(text)}
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

      {/* Right calendar panel: overlay below lg, column from lg up */}
      {panelOpen && (
        <>
          <div
            className="fixed inset-0 z-20 bg-black/40 lg:hidden"
            onClick={() => setPanelOpen(false)}
          />
          <aside
            aria-label="Calendar"
            className="fixed inset-y-0 right-0 z-30 w-80 max-w-[85vw] shrink-0 border-l border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 lg:static lg:z-auto"
          >
            <CalendarPanel
              calendar={calendar}
              anchorDate={today}
              refreshKey={refreshKey}
              changedIds={changedIds}
            />
          </aside>
        </>
      )}
    </div>
  );
}
