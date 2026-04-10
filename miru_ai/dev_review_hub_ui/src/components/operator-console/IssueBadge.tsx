import { cn } from "@/lib/utils";

const BADGE_CONFIG: Record<string, { label: string; className: string }> = {
  false_parallel: { label: "False Parallel", className: "bg-red-500/15 text-red-400 ring-red-500/25" },
  name_mismatch: { label: "Name Mismatch", className: "bg-orange-500/15 text-orange-400 ring-orange-500/25" },
  stat_mismatch: { label: "Stat Mismatch", className: "bg-amber-500/15 text-amber-400 ring-amber-500/25" },
  missing_art: { label: "Missing Art", className: "bg-violet-500/15 text-violet-400 ring-violet-500/25" },
  unverified: { label: "Unverified", className: "bg-slate-500/15 text-slate-400 ring-slate-500/25" },
  new_card: { label: "New Card", className: "bg-sky-500/15 text-sky-400 ring-sky-500/25" },
};

interface IssueBadgeProps {
  issue: string;
}

export function IssueBadge({ issue }: IssueBadgeProps) {
  const config = BADGE_CONFIG[issue];
  if (!config) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1",
        config.className,
      )}
    >
      {config.label}
    </span>
  );
}
