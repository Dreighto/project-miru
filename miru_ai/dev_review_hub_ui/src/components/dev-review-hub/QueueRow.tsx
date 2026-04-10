import { cn } from "@/lib/utils";
import type { QueueItem } from "@/data/mockQueue";
import { StatusDots } from "./StatusDots";

function ThumbPlaceholder({ label }: { label: string }) {
  return (
    <div
      className="flex h-[68px] w-12 shrink-0 items-center justify-center overflow-hidden rounded-md bg-gradient-to-br from-zinc-800 to-zinc-900 ring-1 ring-white/[0.06]"
      aria-hidden
    >
      <span className="text-[10px] font-semibold uppercase tracking-tight text-zinc-500">
        {label.slice(0, 3)}
      </span>
    </div>
  );
}

export interface QueueRowProps {
  item: QueueItem;
  className?: string;
  onSelect?: (item: QueueItem) => void;
}

export function QueueRow({ item, className, onSelect }: QueueRowProps) {
  const metaLine = `${item.setCode} | #${item.cardNumber} | ${item.version}`;

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(item)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(item);
        }
      }}
      style={{ height: 76 }}
      className={cn(
        "flex w-full min-w-0 max-w-full cursor-pointer items-center gap-1.5 overflow-hidden rounded-md bg-drh-surface/80 px-1.5 py-1.5 ring-1 ring-white/[0.04] transition-colors hover:bg-drh-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500/40 active:bg-white/[0.04]",
        className,
      )}
    >
      <div className="shrink-0">
        {item.thumbUrl ? (
          <img
            src={item.thumbUrl}
            alt=""
            width={48}
            height={68}
            className="h-[68px] w-12 rounded-md object-cover ring-1 ring-white/[0.06]"
            loading="lazy"
          />
        ) : (
          <ThumbPlaceholder label={item.setCode} />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-[13px] font-semibold leading-tight text-drh-text">
          {item.name}
        </h2>
        <p className="mt-0.5 truncate text-[11px] leading-tight text-drh-muted">
          {metaLine}
        </p>
      </div>
      <StatusDots segments={item.segments} />
    </article>
  );
}
