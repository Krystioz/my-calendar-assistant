import type { ReactNode } from "react";
import type { CalendarEvent, CalendarTask } from "./api";
import { EventCard, RecordCard, TaskRow, formatDay, formatRange, formatTime, parseLocal, type CardTag } from "./cards";

type Obj = Record<string, unknown>;

function parse(raw: string | null): Obj | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null;
  } catch {
    return null;
  }
}

/** One line of `key: value` pairs for the tool call header. */
export function argsSummary(argumentsRaw: string): string {
  const args = parse(argumentsRaw);
  if (!args) return "";
  const line = Object.entries(args)
    .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
  return line.length > 140 ? `${line.slice(0, 140)}…` : line;
}

const Muted = ({ children }: { children: ReactNode }) => (
  <div className="text-xs text-zinc-500 dark:text-zinc-400">{children}</div>
);

function EventList({ events, empty }: { events: CalendarEvent[]; empty: string }) {
  if (events.length === 0) return <Muted>{empty}</Muted>;
  return (
    <div className="space-y-1">
      {events.map((e) => (
        <EventCard key={e.id} event={e} />
      ))}
    </div>
  );
}

type BusyEvent = CalendarEvent & { blocks_time?: boolean };

function FreeBusy({ args, result }: { args: Obj | null; result: Obj }) {
  const busy = (result.busy as BusyEvent[] | undefined) ?? [];
  const slots = (result.free_slots as { start: string; end: string }[] | undefined) ?? [];
  const ws = typeof args?.start === "string" ? parseLocal(args.start).getTime() : NaN;
  const we = typeof args?.end === "string" ? parseLocal(args.end).getTime() : NaN;
  const span = we - ws;
  const pct = (s: string) => Math.min(100, Math.max(0, ((parseLocal(s).getTime() - ws) / span) * 100));
  return (
    <div className="space-y-1.5 text-xs">
      <div className={result.is_free ? "font-medium text-emerald-700 dark:text-emerald-400" : "font-medium text-amber-700 dark:text-amber-400"}>
        {result.is_free ? "Free" : `Busy (${busy.length})`}
      </div>
      {span > 0 && (
        <div>
          <div className="relative h-3 overflow-hidden rounded bg-emerald-100 dark:bg-emerald-950/50">
            {busy
              // all-day events do not block time
              .filter((e) => e.blocks_time ?? !e.all_day)
              .map((e) => (
                <div
                  key={e.id}
                  title={`${e.title} ${formatRange(e.start, e.end)}`}
                  className="absolute inset-y-0 bg-red-400/80 dark:bg-red-500/70"
                  style={{ left: `${pct(e.start)}%`, width: `${pct(e.end) - pct(e.start)}%` }}
                />
              ))}
          </div>
          <div className="mt-0.5 flex justify-between text-[10px] text-zinc-400">
            <span>{formatDay(args!.start as string)} {formatTime(args!.start as string)}</span>
            <span>{formatTime(args!.end as string)}</span>
          </div>
        </div>
      )}
      {busy.length > 0 && <EventList events={busy} empty="" />}
      {slots.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {slots.map((s) => (
            <span
              key={s.start}
              className="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"
            >
              {formatTime(s.start)}–{formatTime(s.end)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const MUTATIONS: Record<string, [key: string, tag: CardTag]> = {
  create_event: ["created", { label: "added", tone: "green" }],
  create_task: ["created", { label: "added", tone: "green" }],
  delete_event: ["deleted", { label: "removed", tone: "red" }],
  delete_task: ["deleted", { label: "removed", tone: "red" }],
  complete_task: ["completed", { label: "done", tone: "blue" }],
  move_task: ["moved", { label: "moved", tone: "blue" }],
};

const SOFT_ERRORS = new Set(["declined_by_user", "confirmation_required", "already_exists", "not_found"]);

function ErrorLine({ error }: { error: Obj }) {
  const code = String(error.code ?? "error");
  const existing = error.existing as CalendarEvent | CalendarTask | undefined;
  const soft = SOFT_ERRORS.has(code);
  return (
    <div className="space-y-1">
      <div className={`text-xs ${soft ? "text-amber-700 dark:text-amber-400" : "text-red-700 dark:text-red-400"}`}>
        <span className="font-mono font-semibold">{code}</span>
        {error.message ? `: ${String(error.message)}` : ""}
      </div>
      {existing && typeof existing === "object" && (
        <RecordCard record={existing} tag={{ label: "exists", tone: "amber" }} />
      )}
    </div>
  );
}

type ToolResultProps = { name: string; argumentsRaw: string; resultRaw: string | null };

/** Domain view of a tool result; null when there is none, so the caller shows raw JSON. */
export function ToolResult({ name, argumentsRaw, resultRaw }: ToolResultProps) {
  const env = parse(resultRaw);
  if (!env || typeof env.ok !== "boolean") return null;
  if (!env.ok) return env.error && typeof env.error === "object" ? <ErrorLine error={env.error as Obj} /> : null;
  const r = env.result as Obj | undefined;
  if (!r || typeof r !== "object") return null;

  if (name === "search_events" && Array.isArray(r.events)) {
    return (
      <div className="space-y-1">
        <Muted>
          {r.returned as number} of {r.total_matches as number} events
        </Muted>
        <EventList events={r.events as CalendarEvent[]} empty="No events." />
      </div>
    );
  }

  if (name === "free_busy") return <FreeBusy args={parse(argumentsRaw)} result={r} />;

  if (name === "list_tasks" && Array.isArray(r.tasks)) {
    const tasks = r.tasks as CalendarTask[];
    return (
      <div className="space-y-1">
        <Muted>
          {r.returned as number} of {r.total_matches as number} tasks
        </Muted>
        {tasks.length === 0 ? <Muted>No tasks.</Muted> : tasks.map((t) => <TaskRow key={t.id} task={t} />)}
      </div>
    );
  }

  const mutation = MUTATIONS[name];
  if (mutation && r[mutation[0]] && typeof r[mutation[0]] === "object") {
    const conflicts = (r.conflicts as CalendarEvent[] | undefined) ?? [];
    return (
      <div className="space-y-1">
        <RecordCard
          record={r[mutation[0]] as CalendarEvent | CalendarTask}
          tag={r.unchanged ? { label: "unchanged", tone: "zinc" } : mutation[1]}
        />
        {conflicts.map((e) => (
          <EventCard key={e.id} event={e} tag={{ label: "conflict", tone: "amber" }} />
        ))}
      </div>
    );
  }

  if (name === "search_docs" && Array.isArray(r.fragments)) {
    const frags = r.fragments as { id: string; source: string; heading: string; text: string }[];
    if (frags.length === 0) return <Muted>No fragments.</Muted>;
    return (
      <div className="space-y-1">
        {frags.map((f) => (
          <div key={f.id} className="rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs dark:border-zinc-800">
            <div className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
              {f.source}#{f.heading}
            </div>
            <div className="line-clamp-2 text-zinc-700 dark:text-zinc-300">{f.text}</div>
          </div>
        ))}
      </div>
    );
  }

  return null;
}
