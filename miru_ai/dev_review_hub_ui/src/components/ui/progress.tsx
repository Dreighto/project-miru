import { cn } from "@/lib/utils";

interface ProgressProps {
  value: number; // 0–100
  className?: string;
  indicatorClassName?: string;
  /** CSS colour string for the filled portion. Defaults to gold. */
  indicatorColor?: string;
}

/**
 * Lightweight progress bar — shadcn/ui-style API, no Radix dep.
 */
export function Progress({
  value,
  className,
  indicatorClassName,
  indicatorColor,
}: ProgressProps) {
  const clamped = Math.min(Math.max(value ?? 0, 0), 100);
  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn(
        "relative h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]",
        className,
      )}
    >
      <div
        className={cn(
          "h-full rounded-full transition-all duration-700 ease-in-out",
          !indicatorColor && (indicatorClassName ?? "bg-[#c9a84c]/70"),
          indicatorColor && indicatorClassName,
        )}
        style={
          indicatorColor
            ? { width: `${clamped}%`, background: indicatorColor, opacity: 0.7 }
            : { width: `${clamped}%` }
        }
      />
    </div>
  );
}
