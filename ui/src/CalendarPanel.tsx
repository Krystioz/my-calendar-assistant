import { useEffect, useState } from "react";
import {
  fetchCalendar,
  type CalendarBackend,
  type CalendarEvent,
  type CalendarSnapshot,
} from "./api";

type CalendarPanelProps = {
  calendar: CalendarBackend;
  anchorDate: string;
  refreshKey: number;
  changedIds: Set<string>;
};

/** "YYYY-MM-DD" as a local date: new Date("YYYY-MM-DD") would be UTC midnight. */
function parseDay(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function formatDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function addDays(s: string, n: number): string {
  const d = parseDay(s);
  d.setDate(d.getDate() + n);
  return formatDay(d);
}

const dayLabel = (s: string) =>
  parseDay(s).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

const changedClass = (changed: boolean) =>
  changed ? "ring-2 ring-blue-500/70 dark:ring-blue-400/70" : "";

function ChangedTag() {
  return (
    <span className="shrink-0 rounded bg-blue-100 px-1 text-[10px] font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300">
      changed
    </span>
  );
}

export function CalendarPanel({ calendar, anchorDate, refreshKey, changedIds }: CalendarPanelProps) {
  const anchor = anchorDate || formatDay(new Date());
  const [weekOffset, setWeekOffset] = useState(0);
  const [data, setData] = useState<CalendarSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setWeekOffset(0), [anchor]);

  const start = addDays(anchor, weekOffset * 7);
  const end = addDays(start, 7); // exclusive

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // /calendar takes an inclusive end day.
    fetchCalendar(calendar, start, addDays(end, -1))
      .then((snap) => !cancelled && setData(snap))
      .catch((err) => {
        if (cancelled) return;
        setData(null);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [calendar, start, end, refreshKey]);

  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  // shortcut: a multi-day event shows only on its start day; spread it over its days if that matters.
  const eventsOn = (day: string): CalendarEvent[] =>
    (data?.events ?? [])
      .filter((e) => e.start.slice(0, 10) === day)
      .sort((a, b) => Number(b.all_day) - Number(a.all_day) || a.start.localeCompare(b.start));
  const tasks = (data?.tasks ?? [])
    .filter((t) => !t.completed && t.due !== null && t.due < end)
    .sort((a, b) => (a.due ?? "").localeCompare(b.due ?? ""));
  const realToday = formatDay(new Date());

  const navBtn =
    "rounded-md px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800";

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <button type="button" onClick={() => setWeekOffset((w) => w - 1)} className={navBtn} aria-label="Previous week">
          ‹
        </button>
        <span className="text-xs font-medium text-zinc-700 dark:text-zinc-200">
          {dayLabel(start)} – {dayLabel(addDays(end, -1))}
        </span>
        <button type="button" onClick={() => setWeekOffset((w) => w + 1)} className={navBtn} aria-label="Next week">
          ›
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-3 py-3 text-sm">
        {error ? (
          <div className="rounded-lg border border-red-200 bg-red-50/80 px-3 py-2 text-xs text-red-900 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-100">
            The /calendar endpoint is unavailable: {error}
          </div>
        ) : !data && loading ? (
          <div className="text-xs text-zinc-400">Loading calendar...</div>
        ) : (
          <>
            {days.map((day) => {
              const events = eventsOn(day);
              return (
                <section key={day}>
                  <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    {dayLabel(day)}
                  </h3>
                  {events.length === 0 ? (
                    <div className="text-xs text-zinc-400 dark:text-zinc-500">No events</div>
                  ) : (
                    <ul className="space-y-1">
                      {events.map((e) => {
                        const changed = changedIds.has(e.id);
                        return (
                          <li
                            key={e.id}
                            title={e.location || undefined}
                            className={`flex items-center gap-2 rounded-md bg-zinc-100/70 px-2 py-1 text-xs dark:bg-zinc-800/60 ${changedClass(changed)}`}
                          >
                            <span className="w-16 shrink-0 tabular-nums text-zinc-500 dark:text-zinc-400">
                              {e.all_day ? "all day" : `${e.start.slice(11, 16)}–${e.end.slice(11, 16)}`}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-100">{e.title}</span>
                            {e.calendar && (
                              <span className="max-w-20 shrink-0 truncate rounded border border-zinc-200 px-1 text-[10px] text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                                {e.calendar}
                              </span>
                            )}
                            {changed && <ChangedTag />}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })}

            <section>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Open tasks
              </h3>
              {tasks.length === 0 ? (
                <div className="text-xs text-zinc-400 dark:text-zinc-500">No open tasks due</div>
              ) : (
                <ul className="space-y-1">
                  {tasks.map((t) => {
                    const changed = changedIds.has(t.id);
                    const overdue = (t.due ?? "") < (anchorDate || realToday);
                    return (
                      <li
                        key={t.id}
                        title={t.notes || undefined}
                        className={`flex items-center gap-2 rounded-md bg-zinc-100/70 px-2 py-1 text-xs dark:bg-zinc-800/60 ${changedClass(changed)}`}
                      >
                        <span
                          className={`w-16 shrink-0 tabular-nums ${
                            overdue ? "font-medium text-red-600 dark:text-red-400" : "text-zinc-500 dark:text-zinc-400"
                          }`}
                        >
                          {t.due}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-100">{t.title}</span>
                        {changed && <ChangedTag />}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
