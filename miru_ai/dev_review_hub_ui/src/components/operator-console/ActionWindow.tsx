import { CardReviewItem } from "./CardReviewItem";
import type { OperatorQueueItem, QueueState } from "@/lib/queueManager";

interface ActionWindowProps {
  queueState: QueueState;
  onApprove: (item: OperatorQueueItem) => void;
  onReject: (item: OperatorQueueItem) => void;
  onEdit: (item: OperatorQueueItem) => void;
  onImageEvidence: (item: OperatorQueueItem, file: File) => void;
  onLinkEvidence: (item: OperatorQueueItem, url: string) => void;
}

export function ActionWindow({
  queueState,
  onApprove,
  onReject,
  onEdit,
  onImageEvidence,
  onLinkEvidence,
}: ActionWindowProps) {
  return (
    <main className="action-window flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-y-contain px-3 pb-[max(12px,calc(8px+env(safe-area-inset-bottom,0px)))] pt-2">
      {queueState.items.length === 0 && !queueState.isFetching && (
        <div className="flex flex-1 items-center justify-center">
          <p className="text-center text-[13px] text-drh-muted">
            Queue empty — all cards reviewed.
          </p>
        </div>
      )}
      {queueState.isFetching && queueState.items.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
        </div>
      )}
      <div className="flex flex-col gap-2.5">
        {queueState.items.map((item) => (
          <CardReviewItem
            key={item.id}
            item={item}
            isPending={queueState.pendingCardId === item.id}
            conflictBanner={
              queueState.conflictCardId === item.id ? queueState.conflictBanner : null
            }
            onApprove={onApprove}
            onReject={onReject}
            onEdit={onEdit}
            onImageEvidence={onImageEvidence}
            onLinkEvidence={onLinkEvidence}
          />
        ))}
      </div>
    </main>
  );
}
