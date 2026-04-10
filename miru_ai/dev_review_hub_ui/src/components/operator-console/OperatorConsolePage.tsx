import * as React from "react";
import { MissionHeader, type OperatorHelperStatus } from "./MissionHeader";
import { ActionWindow } from "./ActionWindow";
import { HelperDrawer } from "./HelperDrawer";
import { DiagnosticsDrawer } from "./DiagnosticsDrawer";
import { OperatorLegendModal } from "./OperatorLegendModal";
import { ToastBanner } from "./ToastBanner";
import { ReviewDrawer } from "@/components/dev-review-hub/ReviewDrawer";
import { MiruNavBar } from "@/components/hub/MiruNavBar";
import { verifyAction } from "@/lib/verifyAction";
import {
  submitDevTrainingReview,
  type ReviewBarAction,
} from "@/lib/reviewSubmit";
import {
  fetchHelperStatus,
  type HelperStatusResponse,
} from "@/lib/helperClient";
import {
  queueReducer,
  initialQueueState,
  shouldReplenish,
  fetchQueueBatch,
  type OperatorQueueItem,
} from "@/lib/queueManager";

const INITIAL_FETCH = 15;

export function OperatorConsolePage() {
  const [queue, dispatch] = React.useReducer(queueReducer, initialQueueState("OP01"));
  const [activeDrawer, setActiveDrawer] = React.useState<"helper" | "diagnostics" | null>(null);
  const [legendOpen, setLegendOpen] = React.useState(false);
  const [toastMsg, setToastMsg] = React.useState<string | null>(null);
  const [toastVariant, setToastVariant] = React.useState<"success" | "error">("success");
  const [helperStatus, setHelperStatus] = React.useState<OperatorHelperStatus>({
    enabled: false,
    reachable: false,
  });

  const applyHelperStatus = React.useCallback((hs: HelperStatusResponse) => {
    setHelperStatus({
      enabled: Boolean(hs.enabled),
      reachable: Boolean(hs.reachable),
      model: typeof hs.model === "string" ? hs.model : undefined,
      envEnabled: typeof hs.envEnabled === "boolean" ? hs.envEnabled : undefined,
      runtimeOverride:
        typeof hs.runtimeOverride === "boolean" ? hs.runtimeOverride : undefined,
    });
  }, []);
  const [editItem, setEditItem] = React.useState<OperatorQueueItem | null>(null);
  const [editDrawerOpen, setEditDrawerOpen] = React.useState(false);

  // Candidate counts
  const [candidateCounts, setCandidateCounts] = React.useState({
    pending: 0,
    elevated: 0,
    stale: 0,
  });

  // Initial data load
  React.useEffect(() => {
    dispatch({ type: "SET_FETCHING", isFetching: true });

    // Fetch queue, helper status, and candidate counts (throughput lives on Miru Hub).
    Promise.all([
      fetchQueueBatch(queue.setCode, 0, INITIAL_FETCH),
      fetchHelperStatus(),
      fetch(`/api/dev/candidate-queue?card_code_prefix=${queue.setCode}-`, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then((r) => r.json())
        .catch(() => ({ counts: {} })),
    ]).then(([queueData, hs, cq]) => {
      dispatch({ type: "LOAD", items: queueData.items, hasMore: queueData.hasMore });
      applyHelperStatus(hs);
      const counts = cq.counts || {};
      setCandidateCounts({
        pending: (counts.standard ?? 0) + (counts.elevated ?? 0),
        elevated: counts.elevated ?? 0,
        stale: counts.stale ?? 0,
      });
    });
  }, [queue.setCode, applyHelperStatus]);

  // Background replenishment
  React.useEffect(() => {
    if (shouldReplenish(queue)) {
      dispatch({ type: "SET_FETCHING", isFetching: true });
      fetchQueueBatch(queue.setCode, queue.offset).then((data) => {
        dispatch({ type: "APPEND", items: data.items, hasMore: data.hasMore });
      });
    }
  }, [queue]);

  // Drawer mutual exclusion helpers
  const openHelper = React.useCallback(() => {
    setActiveDrawer((prev) => (prev === "helper" ? null : "helper"));
  }, []);
  const openDiagnostics = React.useCallback(() => {
    setActiveDrawer((prev) => (prev === "diagnostics" ? null : "diagnostics"));
  }, []);

  const showToast = React.useCallback((msg: string, variant: "success" | "error" = "success") => {
    setToastMsg(msg);
    setToastVariant(variant);
  }, []);

  // Governed action handler (verify → submit → result)
  const handleAction = React.useCallback(
    async (item: OperatorQueueItem, action: ReviewBarAction) => {
      const cardId = item.cardCode ?? item.id;
      const variantId = item.variants[0]?.id ?? "";

      dispatch({ type: "SET_PENDING", cardId: item.id });

      // Step 1: verify-action pre-flight
      const verification = await verifyAction({ cardId, variantId, action });
      if (!verification.ok) {
        dispatch({
          type: "SET_CONFLICT",
          cardId: item.id,
          banner: verification.banner || "Action blocked by governance.",
        });
        return;
      }

      // Step 2: submit
      const verdict = action === "approve" ? "looks_correct" : "needs_review";
      const result = await submitDevTrainingReview({
        cardId,
        variantId,
        variantKey: item.variants[0]?.variantKey ?? "",
        verdict,
        issues: item.issues,
        because: "",
        source: "operator-console",
        action,
        miruAssetsRelPath: item.variants[0]?.miruAssetsRelPath ?? null,
      });

      if (result.ok) {
        dispatch({ type: "REMOVE", cardId: item.id });
        const label = action === "approve" ? "Approved" : "Rejected";
        showToast(`${label}: ${cardId} ${item.name}`);
      } else {
        dispatch({
          type: "SET_CONFLICT",
          cardId: item.id,
          banner: ("error" in result ? result.error : undefined) || "Submit failed.",
        });
      }
    },
    [showToast],
  );

  const handleApprove = React.useCallback(
    (item: OperatorQueueItem) => handleAction(item, "approve"),
    [handleAction],
  );
  const handleReject = React.useCallback(
    (item: OperatorQueueItem) => handleAction(item, "fix_it"),
    [handleAction],
  );
  const handleEdit = React.useCallback((item: OperatorQueueItem) => {
    setEditItem(item);
    setEditDrawerOpen(true);
  }, []);

  const handleImageEvidence = React.useCallback(
    (_item: OperatorQueueItem, _file: File) => {
      // Image evidence is attached at submit time via the edit flow.
      // For quick action, we note it and open the edit drawer.
      showToast("Open Edit to attach image evidence.", "success");
    },
    [showToast],
  );

  const handleLinkEvidence = React.useCallback(
    (_item: OperatorQueueItem, _url: string) => {
      showToast("Open Edit to attach link evidence.", "success");
    },
    [showToast],
  );

  const handleEditSubmitted = React.useCallback(() => {
    // After edit drawer submits, remove the card and reload.
    if (editItem) {
      dispatch({ type: "REMOVE", cardId: editItem.id });
      showToast(`Submitted: ${editItem.cardCode ?? editItem.id}`);
    }
    setEditDrawerOpen(false);
    setEditItem(null);
  }, [editItem, showToast]);

  const topCardCode = queue.items[0]?.cardCode ?? queue.items[0]?.id ?? null;

  return (
    <div className="mx-auto flex h-dvh max-h-dvh min-h-0 w-full max-w-iphone flex-col overflow-x-hidden bg-drh-bg">
      <MiruNavBar />
      <MissionHeader
        setCode={queue.setCode}
        counts={candidateCounts}
        helperStatus={helperStatus}
        onInfoClick={() => setLegendOpen(true)}
        onHelperClick={openHelper}
        onDiagnosticsClick={openDiagnostics}
      />

      <ActionWindow
        queueState={queue}
        onApprove={handleApprove}
        onReject={handleReject}
        onEdit={handleEdit}
        onImageEvidence={handleImageEvidence}
        onLinkEvidence={handleLinkEvidence}
      />

      <HelperDrawer
        open={activeDrawer === "helper"}
        onOpenChange={(o) => setActiveDrawer(o ? "helper" : null)}
        topCardCode={topCardCode}
        helperStatus={helperStatus}
        onHelperStatusUpdated={applyHelperStatus}
      />

      <DiagnosticsDrawer
        open={activeDrawer === "diagnostics"}
        onOpenChange={(o) => setActiveDrawer(o ? "diagnostics" : null)}
        setCode={queue.setCode}
      />

      <OperatorLegendModal
        open={legendOpen}
        onClose={() => setLegendOpen(false)}
      />

      <ToastBanner
        message={toastMsg}
        variant={toastVariant}
        onDismiss={() => setToastMsg(null)}
      />

      {/* Edit drawer — reuses existing ReviewDrawer for full edit flow */}
      {editItem && (
        <ReviewDrawer
          item={editItem}
          open={editDrawerOpen}
          onOpenChange={setEditDrawerOpen}
          onSubmitted={handleEditSubmitted}
          onAnimationEnd={(isOpen) => {
            if (!isOpen) setEditItem(null);
          }}
        />
      )}
    </div>
  );
}
