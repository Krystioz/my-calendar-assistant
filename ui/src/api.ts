import { Client, type Thread, type ThreadState } from "@langchain/langgraph-sdk";
import type { GraphShape } from "./GraphMap";

export const API_URL =
  import.meta.env.VITE_API_URL || "http://127.0.0.1:2024";
export const ASSISTANT_ID = "calendar_agent";

export const client = new Client({ apiUrl: API_URL });

export type CalendarBackend = "local" | "google";

export type AgentStatus =
  | "success"
  | "not_found"
  | "clarify"
  | "error"
  | "step_limit"
  | "cost_limit";

export type ThreadSettings = {
  title: string;
  calendar: CalendarBackend;
  today: string;
};

export type RawToolCall = {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
};

export type RawMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | null;
  tool_calls?: RawToolCall[];
  tool_call_id?: string;
};

export type ConfirmInterrupt = {
  kind: "confirm";
  question: string;
};

export type ClarifyInterrupt = {
  kind: "clarify";
  question: string;
  citations?: string[];
};

export type AgentInterrupt = ConfirmInterrupt | ClarifyInterrupt;

export type AgentStateValues = {
  goal?: string;
  messages?: RawMessage[];
  status?: AgentStatus | null;
  answer?: string;
  citations?: string[];
  steps?: number;
  cost_usd?: number;
  clarifications?: number;
  next_node?: string;
  __interrupt__?: Array<{ value?: AgentInterrupt }>;
};

export type ToolCallStep = {
  step: number;
  id: string;
  name: string;
  argumentsRaw: string;
  resultRaw: string | null;
};

export type ConversationTurn = {
  id: string;
  userMessage: string;
  assistantAnswer: string | null;
  status: AgentStatus | null;
  citations: string[];
  steps: number | null;
  costUsd: number | null;
  toolCalls: ToolCallStep[];
  nodeSequence: string[];
};

export type GraphStats = {
  visits: Record<string, number>;
  transitions: Record<string, number>;
};

export function getThreadSettings(thread: Thread<AgentStateValues>): ThreadSettings {
  const meta = (thread.metadata ?? {}) as Record<string, unknown>;
  const values = thread.values as AgentStateValues | undefined;
  const calendar: CalendarBackend =
    meta.calendar === "google" ? "google" : "local";
  const today = typeof meta.today === "string" ? meta.today : "";
  const title =
    typeof meta.title === "string" && meta.title.trim()
      ? meta.title.trim()
      : values?.goal?.trim() || `Thread ${thread.thread_id.slice(0, 8)}`;
  return { title, calendar, today };
}

export async function fetchGraphShape(): Promise<GraphShape> {
  const raw = await client.assistants.getGraph(ASSISTANT_ID);
  return {
    nodes: raw.nodes.map((n) => ({ id: String(n.id) })),
    edges: raw.edges.map((e) => ({
      source: String(e.source),
      target: String(e.target),
      ...(e.conditional ? { conditional: true } : {}),
    })),
  };
}

export function computeGraphStats(nodeSequence: string[]): GraphStats {
  const visits: Record<string, number> = {};
  const transitions: Record<string, number> = {};
  for (let i = 0; i < nodeSequence.length; i++) {
    const node = nodeSequence[i];
    visits[node] = (visits[node] ?? 0) + 1;
    if (i > 0) {
      const edgeKey = `${nodeSequence[i - 1]}->${node}`;
      transitions[edgeKey] = (transitions[edgeKey] ?? 0) + 1;
    }
  }
  return { visits, transitions };
}

function isAgentInterrupt(val: unknown): val is AgentInterrupt {
  if (!val || typeof val !== "object") return false;
  const v = val as Record<string, unknown>;
  if (v.kind === "confirm" && typeof v.question === "string") return true;
  if (v.kind === "clarify" && typeof v.question === "string") return true;
  return false;
}

export function extractInterrupt(
  state: Pick<ThreadState<AgentStateValues>, "tasks" | "values"> | null | undefined,
): AgentInterrupt | null {
  if (!state) return null;
  for (const task of state.tasks ?? []) {
    for (const intr of task.interrupts ?? []) {
      if (isAgentInterrupt(intr.value)) return intr.value;
    }
  }
  const fromValues = state.values?.__interrupt__;
  if (Array.isArray(fromValues)) {
    for (const intr of fromValues) {
      if (isAgentInterrupt(intr?.value)) return intr.value;
    }
  }
  return null;
}

export function extractTaskError(
  state: Pick<ThreadState<AgentStateValues>, "tasks"> | null | undefined,
): string | null {
  if (!state) return null;
  for (const task of state.tasks ?? []) {
    if (task.error) return String(task.error);
  }
  return null;
}

export function extractNodeSequenceFromHistory(
  history: ThreadState<AgentStateValues>[],
): string[] {
  const chronological = history.slice().reverse();
  const seq: string[] = [];
  for (const cp of chronological) {
    if (cp.metadata?.source === "fork") continue;
    const nextNodes = Array.isArray(cp.next) ? cp.next : [];
    for (const n of nextNodes) {
      if (n && !n.startsWith("__")) {
        seq.push(n);
      }
    }
  }
  return seq;
}

export type TurnTotals = Record<number, { steps: number; costUsd: number }>;

/** Steps and cost of every turn: state resets both when a clarify answer starts
 * the next turn, so the last checkpoint with `clarifications === k` holds turn k. */
export function extractTurnTotals(history: ThreadState<AgentStateValues>[]): TurnTotals {
  const totals: TurnTotals = {};
  for (const cp of history.slice().reverse()) {
    const v = cp.values;
    if (!v || typeof v.steps !== "number") continue;
    totals[v.clarifications ?? 0] = { steps: v.steps, costUsd: v.cost_usd ?? 0 };
  }
  return totals;
}

function splitNodesByTurn(nodeSequence: string[]): string[][] {
  if (nodeSequence.length === 0) return [];
  const buckets: string[][] = [[]];
  for (let i = 0; i < nodeSequence.length; i++) {
    const node = nodeSequence[i];
    buckets[buckets.length - 1].push(node);
    if (node === "ask_clarification" && i < nodeSequence.length - 1) {
      buckets.push([]);
    }
  }
  return buckets;
}

function safeJsonParse(raw: string | null | undefined): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function buildTurnsFromState(
  values: AgentStateValues | null | undefined,
  interrupt: AgentInterrupt | null,
  fullNodeSequence: string[],
  turnTotals: TurnTotals = {},
): ConversationTurn[] {
  const v = values ?? {};
  const messages = Array.isArray(v.messages) ? v.messages : [];
  const nodeBuckets = splitNodesByTurn(fullNodeSequence);

  type WorkingTurn = ConversationTurn & {
    assistantMsgCount: number;
    clarifyDelivered: { answer: string; citations: string[] } | null;
  };

  const turns: WorkingTurn[] = [];
  let prevWasPlainAssistant = false;

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg.role === "system" || msg.role === "tool") {
      continue;
    }

    if (msg.role === "user") {
      const content = msg.content ?? "";
      if (
        prevWasPlainAssistant &&
        content.startsWith("Call `finish` now with the appropriate")
      ) {
        prevWasPlainAssistant = false;
        continue;
      }
      prevWasPlainAssistant = false;
      const idx = turns.length;
      turns.push({
        id: `turn-${idx}`,
        userMessage: content,
        assistantAnswer: null,
        status: null,
        citations: [],
        steps: null,
        costUsd: null,
        toolCalls: [],
        nodeSequence: nodeBuckets[idx] ?? [],
        assistantMsgCount: 0,
        clarifyDelivered: null,
      });
      continue;
    }

    if (msg.role === "assistant" && turns.length > 0) {
      const current = turns[turns.length - 1];
      current.assistantMsgCount += 1;
      const calls = Array.isArray(msg.tool_calls) ? msg.tool_calls : [];
      // Results are the tool messages right after this assistant message: ids
      // are only unique within one model reply, not across the conversation.
      const toolResults = new Map<string, string>();
      for (let j = i + 1; j < messages.length && messages[j].role === "tool"; j++) {
        const id = messages[j].tool_call_id;
        if (id) toolResults.set(id, messages[j].content ?? "");
      }
      const stepId = (callId: string) =>
        `${turns.length}-${current.assistantMsgCount}-${callId}`;

      if (calls.length === 0) {
        prevWasPlainAssistant = true;
        // A model step without a tool call still counts: show it, with the nudge.
        current.toolCalls.push({
          step: current.assistantMsgCount,
          id: `text-${turns.length}-${current.assistantMsgCount}`,
          name: "plain text reply (no tool call)",
          argumentsRaw: JSON.stringify({ text: msg.content ?? "" }),
          resultRaw: JSON.stringify(
            "Not accepted as an answer: the app asked the model to call finish.",
          ),
        });
        continue;
      }
      prevWasPlainAssistant = false;

      for (const tc of calls) {
        const fnName = tc.function?.name ?? "";
        const argsRaw = tc.function?.arguments ?? "{}";
        const resultRaw = toolResults.get(tc.id) ?? null;

        if (fnName === "finish") {
          const parsedArgs = safeJsonParse(argsRaw);
          const parsedRes = safeJsonParse(resultRaw);
          const resBody = parsedRes?.result as Record<string, unknown> | undefined;

          if (
            parsedArgs?.outcome === "clarify" &&
            (resultRaw === null ||
              (parsedRes?.ok === true && resBody?.delivered === true))
          ) {
            current.clarifyDelivered = {
              answer: typeof parsedArgs.answer === "string" ? parsedArgs.answer : "",
              citations: Array.isArray(parsedArgs.citations)
                ? parsedArgs.citations.filter((c): c is string => typeof c === "string")
                : [],
            };
            continue;
          }

          // A rejected finish has a tool result (e.g. unverified citations);
          // an accepted one has none, because the run ends there.
          current.toolCalls.push({
            step: current.assistantMsgCount,
            id: stepId(tc.id),
            name: fnName,
            argumentsRaw: argsRaw,
            resultRaw:
              resultRaw ?? JSON.stringify("No tool result: the run ended here."),
          });
          continue;
        }

        current.toolCalls.push({
          step: current.assistantMsgCount,
          id: stepId(tc.id),
          name: fnName,
          argumentsRaw: argsRaw,
          resultRaw,
        });
      }
    }
  }

  if (turns.length === 0 && v.goal) {
    turns.push({
      id: "turn-0",
      userMessage: v.goal,
      assistantAnswer: null,
      status: null,
      citations: [],
      steps: null,
      costUsd: null,
      toolCalls: [],
      nodeSequence: nodeBuckets[0] ?? [],
      assistantMsgCount: 0,
      clarifyDelivered: null,
    });
  }

  return turns.map((t, idx) => {
    const isLast = idx === turns.length - 1;
    if (!isLast) {
      return {
        id: t.id,
        userMessage: t.userMessage,
        assistantAnswer: t.clarifyDelivered?.answer ?? "",
        status: "clarify",
        citations: t.clarifyDelivered?.citations ?? [],
        steps: turnTotals[idx]?.steps ?? t.assistantMsgCount,
        costUsd: turnTotals[idx]?.costUsd ?? null,
        toolCalls: t.toolCalls,
        nodeSequence: t.nodeSequence,
      };
    }

    if (interrupt?.kind === "clarify") {
      return {
        id: t.id,
        userMessage: t.userMessage,
        assistantAnswer: interrupt.question,
        status: "clarify",
        citations: interrupt.citations ?? [],
        steps: v.steps ?? t.assistantMsgCount,
        costUsd: v.cost_usd ?? null,
        toolCalls: t.toolCalls,
        nodeSequence: t.nodeSequence,
      };
    }

    if (interrupt?.kind === "confirm") {
      return {
        id: t.id,
        userMessage: t.userMessage,
        assistantAnswer: null,
        status: null,
        citations: [],
        steps: v.steps ?? t.assistantMsgCount,
        costUsd: v.cost_usd ?? null,
        toolCalls: t.toolCalls,
        nodeSequence: t.nodeSequence,
      };
    }

    return {
      id: t.id,
      userMessage: t.userMessage,
      assistantAnswer: v.status ? (v.answer ?? "") : null,
      status: v.status ?? null,
      citations: Array.isArray(v.citations) ? v.citations : [],
      steps: v.steps ?? (v.status ? t.assistantMsgCount : null),
      costUsd: v.cost_usd ?? null,
      toolCalls: t.toolCalls,
      nodeSequence: t.nodeSequence,
    };
  });
}
