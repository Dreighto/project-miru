import { cn } from "@/lib/utils";
import type { QueueItem, ReviewSegment } from "@/data/mockQueue";

const LABELS = ["A", "B", "C", "D", "E", "F"] as const;

function segmentClass(state: ReviewSegment) {
  switch (state) {
    case "done":
      return "bg-emerald-500/75";
    case "pending":
      return "bg-amber-400/85";
    default:
      return "bg-zinc-700/90";
  }
}

export interface StatusDotsProps {
  segments: QueueItem["segments"];
  className?: string;
}

export function StatusDots({ segments, className }: StatusDotsProps) {
  return (
    <div
      className={cn("flex shrink-0 items-center gap-1", className)}
      aria-label="Review segment status A through F"
    >
      {segments.map((seg, i) => (
        <span
          key={LABELS[i]}
          className={cn(
            "h-2 w-2 rounded-full ring-1 ring-black/20",
            segmentClass(seg),
          )}
          title={`${LABELS[i]}: ${seg}`}
        />
      ))}
    </div>
  );
}
