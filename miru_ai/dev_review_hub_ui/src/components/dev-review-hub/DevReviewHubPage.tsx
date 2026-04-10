import * as React from "react";
import { StickyReviewHeader } from "./StickyReviewHeader";
import { QueueAnimatedList } from "./QueueAnimatedList";
import { ReviewDrawer } from "./ReviewDrawer";
import { MOCK_FILTER_LABEL } from "@/data/mockQueue";
import type { QueueItem } from "@/data/mockQueue";

type QueueResponse = {
  items?: QueueItem[];
  stats?: { reviewedCount?: number; queueTotal?: number };
  error?: string;
};

export function DevReviewHubPage() {
  const [items, setItems] = React.useState<QueueItem[]>([]);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [reviewed, setReviewed] = React.useState(0);
  const [total, setTotal] = React.useState(0);
  const [selectedCard, setSelectedCard] = React.useState<QueueItem | null>(null);
  const [drawerOpen, setDrawerOpen] = React.useState(false);

  const loadQueue = React.useCallback(() => {
    setLoadError(null);
    void fetch("/api/dev/training-review/queue", {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "miru-client-nav" },
    })
      .then(async (r) => {
        const data = (await r.json()) as QueueResponse;
        if (!r.ok) {
          throw new Error(data.error || `HTTP ${r.status}`);
        }
        return data;
      })
      .then((data) => {
        setItems(Array.isArray(data.items) ? data.items : []);
        const st = data.stats || {};
        setReviewed(typeof st.reviewedCount === "number" ? st.reviewedCount : 0);
        setTotal(typeof st.queueTotal === "number" ? st.queueTotal : 0);
      })
      .catch((e: Error) => {
        setLoadError(e.message || "Queue load failed");
        setItems([]);
      });
  }, []);

  React.useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  const openRow = React.useCallback((item: QueueItem) => {
    setSelectedCard(item);
    setDrawerOpen(true);
  }, []);

  return (
    <div className="mx-auto flex h-[min(480px,70svh)] w-full min-w-0 max-w-iphone flex-col overflow-hidden overflow-x-hidden bg-drh-bg">
      <StickyReviewHeader
        filterLabel={MOCK_FILTER_LABEL}
        reviewed={reviewed}
        total={total > 0 ? total : items.length}
      />
      <main className="flex min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain px-2 pb-[max(12px,calc(8px+env(safe-area-inset-bottom,0px)))] pt-1.5">
        {loadError ? (
          <p className="px-1 text-center text-[12px] text-amber-400/90" role="status">
            {loadError}
          </p>
        ) : null}
        <QueueAnimatedList items={items} onSelectCard={openRow} />
      </main>

      {selectedCard ? (
        <ReviewDrawer
          item={selectedCard}
          open={drawerOpen}
          onOpenChange={setDrawerOpen}
          onSubmitted={loadQueue}
          onAnimationEnd={(isOpen) => {
            if (!isOpen) setSelectedCard(null);
          }}
        />
      ) : null}
    </div>
  );
}
