import { useState } from "react";

interface Props {
  open: boolean;
  verdict: "correct" | "wrong";
  isSubmitting?: boolean;
  onCancel: () => void;
  onConfirm: (sourcesChecked: string[]) => void;
}

const SOURCES = ["bandai", "tcgplayer", "catalog"] as const;
type Source = (typeof SOURCES)[number];

export function SourceAckModal({ open, verdict, isSubmitting, onCancel, onConfirm }: Props) {
  const [checked, setChecked] = useState<Record<Source, boolean>>({
    bandai: false,
    tcgplayer: false,
    catalog: false,
  });

  if (!open) return null;

  const selected = SOURCES.filter((s) => checked[s]);
  const canCommit = selected.length > 0 && !isSubmitting;

  function toggle(s: Source) {
    setChecked((prev) => ({ ...prev, [s]: !prev[s] }));
  }

  function commit() {
    if (!canCommit) return;
    onConfirm(selected);
  }

  const heading =
    verdict === "wrong"
      ? "Override verifier — record sources you checked"
      : "Promote to canon — record sources you checked";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={heading}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
    >
      <div className="w-full max-w-md rounded-md border border-zinc-700 bg-zinc-950 p-5 shadow-xl">
        <h3 className="text-sm font-medium text-zinc-100">{heading}</h3>
        <p className="mt-1 text-[12px] text-zinc-400">
          Verdict commits only after you tick at least one source. Five-second forcing
          function, not friction — just confirms what you actually reviewed.
        </p>
        <fieldset className="mt-4 space-y-2">
          <legend className="sr-only">Sources checked</legend>
          {SOURCES.map((s) => (
            <label
              key={s}
              className="flex cursor-pointer items-center gap-2 rounded border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-[13px] text-zinc-200 hover:border-zinc-600"
            >
              <input
                type="checkbox"
                checked={checked[s]}
                onChange={() => toggle(s)}
                className="h-4 w-4 accent-amber-300"
              />
              <span className="capitalize">{s}</span>
            </label>
          ))}
        </fieldset>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-zinc-700 bg-zinc-900/60 px-3 py-1.5 text-[12px] text-zinc-300 hover:border-zinc-500"
            disabled={isSubmitting}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={commit}
            disabled={!canCommit}
            className="rounded border border-amber-300/50 bg-amber-400/20 px-3 py-1.5 text-[12px] font-medium text-amber-200 disabled:cursor-not-allowed disabled:opacity-40 hover:enabled:bg-amber-400/30"
          >
            {isSubmitting ? "Committing…" : "Commit verdict"}
          </button>
        </div>
      </div>
    </div>
  );
}
