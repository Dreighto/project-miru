import type { Verdict } from "@/lib/api/shadow-review";

interface Props {
  onVerdict: (verdict: Verdict) => void;
  disabled?: boolean;
}

export function VerdictButtons({ onVerdict, disabled }: Props) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Operator verdict">
      <button
        type="button"
        onClick={() => onVerdict("correct")}
        disabled={disabled}
        className="rounded border border-emerald-400/50 bg-emerald-400/15 px-4 py-2 text-[12px] font-medium text-emerald-200 disabled:cursor-not-allowed disabled:opacity-40 hover:enabled:bg-emerald-400/25"
      >
        Correct
      </button>
      <button
        type="button"
        onClick={() => onVerdict("wrong")}
        disabled={disabled}
        className="rounded border border-red-400/50 bg-red-400/15 px-4 py-2 text-[12px] font-medium text-red-200 disabled:cursor-not-allowed disabled:opacity-40 hover:enabled:bg-red-400/25"
      >
        Wrong
      </button>
      <button
        type="button"
        onClick={() => onVerdict("defer")}
        disabled={disabled}
        className="rounded border border-zinc-600 bg-zinc-800/50 px-4 py-2 text-[12px] font-medium text-zinc-300 disabled:cursor-not-allowed disabled:opacity-40 hover:enabled:bg-zinc-800/80"
      >
        Defer / Need research
      </button>
    </div>
  );
}
