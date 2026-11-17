import { createContext, useContext } from "react";
import type { CalendarEvent, CalendarTask } from "./api";

/** The conversation's "Today" override ("" = real date), so task urgency matches the calendar panel. */
export const TodayContext = createContext("");

/** Naive local ISO -> Date. `new Date("YYYY-MM-DD")` is UTC midnight, so build date-only values by hand. */
export function parseLocal(s: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(s);
}

const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

export const formatDay = (s: string) => dayFmt.format(parseLocal(s));
export const formatTime = (s: string) => timeFmt.format(parseLocal(s));

export function formatRange(start: string, end: string, allDay = false): string {
  if (allDay) return `${formatDay(start)} · all day`;
  const sameDay = start.slice(0, 10) === end.slice(0, 10);
  return sameDay
    ? `${formatDay(start)} · ${formatTime(start)}–${formatTime(end)}`
    : `${formatDay(start)} ${formatTime(start)} – ${formatDay(end)} ${formatTime(end)}`;
}

export type Tone = "green" | "red" | "amber" | "blue" | "zinc";

const toneClass: Record<Tone, string> = {
  green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200",
  red: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  blue: "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200",
  zinc: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
};

export type CardTag = { label: string; tone: Tone };

export function Badge({ label, tone }: CardTag) {
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${toneClass[tone]}`}>
      {label}
    </span>
  );
}

export function EventCard({ event, tag }: { event: CalendarEvent; tag?: CardTag }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center gap-1.5">
        {tag && <Badge {...tag} />}
        <span className="truncate font-medium text-zinc-900 dark:text-zinc-100">{event.title}</span>
        {event.calendar && <Badge label={event.calendar} tone="zinc" />}
      </div>
      <div className="mt-0.5 text-zinc-500 dark:text-zinc-400">
        {formatRange(event.start, event.end, event.all_day)}
        {event.location && ` · ${event.location}`}
      </div>
    </div>
  );
}

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function TaskRow({ task, tag }: { task: CalendarTask; tag?: CardTag }) {
  const today = useContext(TodayContext) || todayIso();
  // ISO dates compare correctly as strings.
  const dueClass =
    task.completed || !task.due
      ? "text-zinc-500 dark:text-zinc-400"
      : task.due < today
        ? "font-medium text-red-600 dark:text-red-400"
        : task.due === today
          ? "font-medium text-amber-600 dark:text-amber-400"
          : "text-zinc-500 dark:text-zinc-400";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs dark:border-zinc-800 dark:bg-zinc-950">
      <span
        aria-label={task.completed ? "completed" : "open"}
        className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border text-[9px] ${
          task.completed
            ? "border-emerald-500 bg-emerald-500 text-white"
            : "border-zinc-400 dark:border-zinc-600"
        }`}
      >
        {task.completed ? "✓" : ""}
      </span>
      {tag && <Badge {...tag} />}
      <span
        className={`min-w-0 flex-1 truncate ${
          task.completed ? "text-zinc-500 line-through dark:text-zinc-400" : "text-zinc-900 dark:text-zinc-100"
        }`}
      >
        {task.title}
      </span>
      {task.task_list && <Badge label={task.task_list} tone="zinc" />}
      {task.due && (
        <span className={dueClass}>{task.due === today ? "today" : formatDay(task.due)}</span>
      )}
    </div>
  );
}

/** A `target` from a confirm interrupt is an event when it has a start. */
export function RecordCard({ record, tag }: { record: CalendarEvent | CalendarTask; tag?: CardTag }) {
  return "start" in record ? <EventCard event={record} tag={tag} /> : <TaskRow task={record} tag={tag} />;
}
