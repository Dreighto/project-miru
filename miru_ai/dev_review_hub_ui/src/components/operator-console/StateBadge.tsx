import { cn } from "@/lib/utils";

interface StateBadgeProps {
  state: "live" | "staged";
}

export function StateBadge({ state }: StateBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1",
        state === "live"
          ? "bg-emerald-500/15 text-emerald-400 ring-emerald-500/25"
          : "bg-amber-500/15 text-amber-400 ring-amber-500/25",
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          state === "live" ? "bg-emerald-400" : "bg-amber-400",
        )}
      />
      {state === "live" ? "Live" : "Staged"}
    </span>
  );
}
