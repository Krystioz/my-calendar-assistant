import { useEffect, useRef, useState } from "react";
import { fetchDocChunk, type DocChunk } from "./api";

type Load = { state: "idle" | "loading" } | { state: "ok"; chunk: DocChunk } | { state: "error"; message: string };

/** Citation id chip; click opens a popover with the cited doc chunk, fetched once. */
export function CitationChip({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<Load>({ state: "idle" });
  const root = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle() {
    setOpen((prev) => !prev);
    // Fetch on first open; an error retries on the next open.
    if (!open && (load.state === "idle" || load.state === "error")) {
      setLoad({ state: "loading" });
      fetchDocChunk(id).then(
        (chunk) => setLoad({ state: "ok", chunk }),
        (err: unknown) => setLoad({ state: "error", message: String(err instanceof Error ? err.message : err) }),
      );
    }
  }

  return (
    <span ref={root} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="rounded-full border border-zinc-200 bg-zinc-50 px-2.5 py-0.5 font-mono text-[11px] text-zinc-600 hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600"
      >
        {id}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={`Citation ${id}`}
          className="absolute left-0 top-full z-20 mt-1 w-80 max-w-[80vw] rounded-lg border border-zinc-200 bg-white p-2.5 text-xs shadow-lg dark:border-zinc-800 dark:bg-zinc-950"
        >
          {load.state === "loading" && <div className="text-zinc-400">Loading...</div>}
          {load.state === "error" && <div className="text-red-600 dark:text-red-400">{load.message}</div>}
          {load.state === "ok" && (
            <>
              <div className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">{load.chunk.source}</div>
              <div className="mb-1 font-medium text-zinc-900 dark:text-zinc-100">{load.chunk.heading}</div>
              <div className="max-h-60 overflow-y-auto whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
                {load.chunk.text}
              </div>
            </>
          )}
        </div>
      )}
    </span>
  );
}
