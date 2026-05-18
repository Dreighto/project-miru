import type { QueueItem } from "@/lib/api/shadow-review";

interface Props {
  items: QueueItem[];
  selectedKey: string | null;
  onSelect: (item: QueueItem) => void;
}

function itemKey(item: QueueItem): string {
  return `${item.canonical_code}::${item.print_id}::${item.contributing_model}`;
}

function statusColor(status: string): string {
  switch (status) {
    case "review-ready":
      return "border-amber-400/60 bg-amber-400/5";
    case "experimental":
      return "border-zinc-700 bg-zinc-900/40";
    default:
      return "border-zinc-800 bg-zinc-950/60";
  }
}

function statusBadge(status: string): { label: string; cls: string } {
  switch (status) {
    case "review-ready":
      return { label: "REVIEW-READY", cls: "text-amber-300 bg-amber-400/15" };
    case "experimental":
      return { label: "EXPERIMENTAL", cls: "text-zinc-400 bg-zinc-800/60" };
    case "promoted":
      return { label: "PROMOTED", cls: "text-emerald-300 bg-emerald-400/15" };
    case "rejected":
      return { label: "REJECTED", cls: "text-red-300 bg-red-400/15" };
    default:
      return { label: status.toUpperCase(), cls: "text-zinc-500 bg-zinc-800/60" };
  }
}

export function ReviewQueueList({ items, selectedKey, onSelect }: Props) {
  if (items.length === 0) {
    return (
      <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-4 text-sm text-zinc-400">
        Queue is empty. No rows in <code>review-ready</code> or{" "}
        <code>experimental + inconclusive</code> state.
      </div>
    );
  }

  return (
    <ul className="space-y-1" role="list" aria-label="Review queue">
      {items.map((item) => {
        const key = itemKey(item);
        const active = key === selectedKey;
        const badge = statusBadge(item.promotion_status);
        return (
          <li key={key}>
            <button
              type="button"
              onClick={() => onSelect(item)}
              className={[
                "w-full rounded border px-3 py-2 text-left transition-colors",
                statusColor(item.promotion_status),
                active
                  ? "ring-1 ring-amber-300/60"
                  : "hover:border-zinc-600",
              ].join(" ")}
              aria-current={active ? "true" : undefined}
            >
              <div className="flex items-baseline justify-between gap-2">
                <code className="font-mono text-xs text-zinc-200">
                  {item.canonical_code}
                  {item.print_id !== item.canonical_code ? ` · ${item.print_id}` : ""}
                </code>
                <span className={`rounded px-1.5 py-0.5 text-[10px] tracking-wide ${badge.cls}`}>
                  {badge.label}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-zinc-400">
                <span>
                  conf <span className="font-mono text-zinc-200">{item.confidence_score.toFixed(2)}</span>
                </span>
                <span>
                  inconclusive{" "}
                  <span className="font-mono text-zinc-200">{item.inconclusive_field_count}</span>
                </span>
                <span className="font-mono text-[10px] text-zinc-500">
                  {item.contributing_model}
                </span>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
