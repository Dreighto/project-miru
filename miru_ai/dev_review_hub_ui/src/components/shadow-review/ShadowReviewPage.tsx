import { useCallback, useEffect, useState } from "react";

import { EvidencePanel } from "./EvidencePanel";
import { ReviewQueueList } from "./ReviewQueueList";
import { SourceAckModal } from "./SourceAckModal";
import { VerdictButtons } from "./VerdictButtons";
import {
  fetchItem,
  fetchQueue,
  submitVerdict,
  type EvidenceItem,
  type QueueItem,
  type Verdict,
} from "@/lib/api/shadow-review";

function keyOf(item: QueueItem): string {
  return `${item.canonical_code}::${item.print_id}::${item.contributing_model}`;
}

export function ShadowReviewPage() {
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueError, setQueueError] = useState<string | null>(null);

  const [selected, setSelected] = useState<QueueItem | null>(null);
  const [item, setItem] = useState<EvidenceItem | null>(null);
  const [itemLoading, setItemLoading] = useState(false);
  const [itemError, setItemError] = useState<string | null>(null);

  // Source-ack modal state — only opens for correct/wrong.
  const [ackOpen, setAckOpen] = useState(false);
  const [ackVerdict, setAckVerdict] = useState<"correct" | "wrong">("correct");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastVerdict, setLastVerdict] = useState<{
    key: string;
    new_promotion_status: string;
  } | null>(null);

  const reloadQueue = useCallback(async () => {
    setQueueLoading(true);
    setQueueError(null);
    try {
      const { items } = await fetchQueue();
      setQueue(items);
    } catch (e) {
      setQueueError(e instanceof Error ? e.message : String(e));
    } finally {
      setQueueLoading(false);
    }
  }, []);

  useEffect(() => {
    void reloadQueue();
  }, [reloadQueue]);

  const onSelect = useCallback(async (q: QueueItem) => {
    setSelected(q);
    setItem(null);
    setItemError(null);
    setItemLoading(true);
    try {
      const detail = await fetchItem(q.canonical_code, q.print_id, q.contributing_model);
      setItem(detail);
    } catch (e) {
      setItemError(e instanceof Error ? e.message : String(e));
    } finally {
      setItemLoading(false);
    }
  }, []);

  const commit = useCallback(
    async (verdict: Verdict, sources: string[]) => {
      if (!selected) return;
      setSubmitting(true);
      setSubmitError(null);
      try {
        const resp = await submitVerdict({
          canonical_code: selected.canonical_code,
          print_id: selected.print_id,
          contributing_model: selected.contributing_model,
          verdict,
          sources_checked: sources,
        });
        setLastVerdict({ key: keyOf(selected), new_promotion_status: resp.new_promotion_status });
        // Defer keeps the row in the queue. Correct/wrong moves it out — refetch.
        if (verdict !== "defer") {
          setSelected(null);
          setItem(null);
        }
        await reloadQueue();
      } catch (e) {
        setSubmitError(e instanceof Error ? e.message : String(e));
      } finally {
        setSubmitting(false);
      }
    },
    [reloadQueue, selected],
  );

  const onVerdict = useCallback(
    (v: Verdict) => {
      if (v === "defer") {
        void commit("defer", []);
        return;
      }
      setAckVerdict(v);
      setAckOpen(true);
    },
    [commit],
  );

  const onAckConfirm = useCallback(
    (sources: string[]) => {
      void commit(ackVerdict, sources).then(() => setAckOpen(false));
    },
    [ackVerdict, commit],
  );

  const selectedKey = selected ? keyOf(selected) : null;

  return (
    <div className="mx-auto flex min-h-screen max-w-[1400px] gap-4 px-4 py-6">
      {/* Queue column */}
      <aside className="flex w-[360px] shrink-0 flex-col gap-2">
        <header className="flex items-baseline justify-between">
          <h1 className="text-sm font-medium text-zinc-100">Shadow-loop review queue</h1>
          <button
            type="button"
            onClick={() => void reloadQueue()}
            className="rounded border border-zinc-700 bg-zinc-900/60 px-2 py-0.5 text-[11px] text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
            disabled={queueLoading}
          >
            {queueLoading ? "…" : "Refresh"}
          </button>
        </header>
        {queueError && (
          <div className="rounded border border-red-400/40 bg-red-400/10 px-3 py-2 text-[12px] text-red-200">
            {queueError}
          </div>
        )}
        {queueLoading && queue.length === 0 ? (
          <div className="text-[12px] text-zinc-500">Loading queue…</div>
        ) : (
          <ReviewQueueList items={queue} selectedKey={selectedKey} onSelect={onSelect} />
        )}
      </aside>

      {/* Evidence column */}
      <main className="flex flex-1 flex-col gap-3">
        {lastVerdict && (
          <div className="rounded border border-emerald-400/40 bg-emerald-400/10 px-3 py-2 text-[12px] text-emerald-200">
            Verdict recorded. Row now: <code>{lastVerdict.new_promotion_status}</code>
          </div>
        )}
        {submitError && (
          <div className="rounded border border-red-400/40 bg-red-400/10 px-3 py-2 text-[12px] text-red-200">
            Verdict submission failed: {submitError}
          </div>
        )}
        {!selected && (
          <div className="rounded border border-dashed border-zinc-800 bg-zinc-950/40 p-6 text-center text-[13px] text-zinc-500">
            Select a row from the queue to see its evidence panel.
          </div>
        )}
        {selected && itemLoading && (
          <div className="text-[12px] text-zinc-500">Loading evidence…</div>
        )}
        {selected && itemError && (
          <div className="rounded border border-red-400/40 bg-red-400/10 px-3 py-2 text-[12px] text-red-200">
            {itemError}
          </div>
        )}
        {selected && item && (
          <EvidencePanel item={item}>
            <div className="mt-2 border-t border-zinc-800 pt-3">
              <VerdictButtons onVerdict={onVerdict} disabled={submitting} />
            </div>
          </EvidencePanel>
        )}
      </main>

      <SourceAckModal
        open={ackOpen}
        verdict={ackVerdict}
        isSubmitting={submitting}
        onCancel={() => setAckOpen(false)}
        onConfirm={onAckConfirm}
      />
    </div>
  );
}
