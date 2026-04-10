import { motion } from "framer-motion";
import { SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { miruEyebrowClass } from "@/components/shell/MiruPageHeader";

export interface StickyReviewHeaderProps {
  filterLabel: string;
  reviewed: number;
  total: number;
  className?: string;
}

export function StickyReviewHeader({
  filterLabel,
  reviewed,
  total,
  className,
}: StickyReviewHeaderProps) {
  return (
    <motion.header
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        "sticky top-0 z-20 border-b border-drh-stroke/60 bg-drh-bg/85 backdrop-blur-xl backdrop-saturate-150",
        "pt-1.5",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2 px-2 pb-1.5 pt-0.5">
        <div className="min-w-0 flex-1">
          <p className={miruEyebrowClass}>Training</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[13px] font-semibold leading-tight text-drh-text">
              {filterLabel}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="shrink-0 text-drh-muted"
              aria-label="Filter (coming soon)"
              disabled
            >
              <SlidersHorizontal className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <div
          className="shrink-0 rounded-full bg-zinc-800/90 px-2.5 py-1 text-[11px] font-medium tabular-nums leading-none text-drh-text ring-1 ring-white/[0.08]"
          aria-live="polite"
        >
          {reviewed} / {total} reviewed
        </div>
      </div>
    </motion.header>
  );
}
