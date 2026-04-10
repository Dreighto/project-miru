import { cn } from "@/lib/utils";
import { StateBadge } from "./StateBadge";
import { IssueBadge } from "./IssueBadge";
import { EvidencePanel } from "./EvidencePanel";
import type { OperatorQueueItem } from "@/lib/queueManager";

interface CardReviewItemProps {
  item: OperatorQueueItem;
  isPending: boolean;
  conflictBanner: string | null;
  onApprove: (item: OperatorQueueItem) => void;
  onReject: (item: OperatorQueueItem) => void;
  onEdit: (item: OperatorQueueItem) => void;
  onImageEvidence: (item: OperatorQueueItem, file: File) => void;
  onLinkEvidence: (item: OperatorQueueItem, url: string) => void;
}

export function CardReviewItem({
  item,
  isPending,
  conflictBanner,
  onApprove,
  onReject,
  onEdit,
  onImageEvidence,
  onLinkEvidence,
}: CardReviewItemProps) {
  return (
    <article
      className={cn(
        "relative rounded-lg border bg-drh-surface/80 p-3 ring-1 transition-all duration-200",
        conflictBanner
          ? "border-red-500/40 ring-red-500/20"
          : "border-transparent ring-white/[0.04]",
        isPending && "pointer-events-none opacity-40",
      )}
    >
      {/* Spinner overlay */}
      {isPending && (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/20">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
        </div>
      )}

      {/* Card identity row */}
      <div className="flex items-start gap-2.5">
        {/* Thumbnail */}
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
            <div className="flex h-[68px] w-12 items-center justify-center rounded-md bg-gradient-to-br from-zinc-800 to-zinc-900 ring-1 ring-white/[0.06]">
              <span className="text-[10px] font-semibold uppercase text-zinc-500">
                {item.setCode.slice(0, 3)}
              </span>
            </div>
          )}
        </div>

        {/* Identity + badges */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-bold text-amber-400">{item.cardCode || item.id}</span>
            <StateBadge state={item.state} />
          </div>
          <h3 className="mt-0.5 truncate text-[13px] font-semibold leading-tight text-drh-text">
            {item.name}
          </h3>
          {item.issues.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {item.issues.map((iss) => (
                <IssueBadge key={iss} issue={iss} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Context sentence */}
      <p className="mt-2 text-[11px] leading-relaxed text-drh-muted">
        {item.contextSentence}
      </p>

      {/* Conflict banner */}
      {conflictBanner && (
        <div className="mt-2 rounded-md bg-red-500/10 px-3 py-2 text-[11px] font-medium text-red-400 ring-1 ring-red-500/20">
          {conflictBanner}
        </div>
      )}

      {/* Action bar */}
      <div className="mt-2.5 grid grid-cols-3 gap-2">
        <button
          type="button"
          data-action="approve"
          onClick={() => onApprove(item)}
          className="flex h-11 items-center justify-center rounded-lg bg-emerald-500/15 text-[12px] font-bold uppercase tracking-wide text-emerald-400 ring-1 ring-emerald-500/25 transition-colors active:bg-emerald-500/25"
        >
          Approve
        </button>
        <button
          type="button"
          data-action="reject"
          onClick={() => onReject(item)}
          className="flex h-11 items-center justify-center rounded-lg bg-red-500/15 text-[12px] font-bold uppercase tracking-wide text-red-400 ring-1 ring-red-500/25 transition-colors active:bg-red-500/25"
        >
          Reject
        </button>
        <button
          type="button"
          data-action="edit"
          onClick={() => onEdit(item)}
          className="flex h-11 items-center justify-center rounded-lg bg-white/[0.04] text-[12px] font-bold uppercase tracking-wide text-drh-muted ring-1 ring-white/[0.06] transition-colors active:bg-white/[0.08]"
        >
          Edit
        </button>
      </div>

      {/* Evidence panel (collapsed by default) */}
      <div className="mt-1">
        <EvidencePanel
          onImageSelected={(file) => onImageEvidence(item, file)}
          onLinkProvided={(url) => onLinkEvidence(item, url)}
        />
      </div>
    </article>
  );
}
