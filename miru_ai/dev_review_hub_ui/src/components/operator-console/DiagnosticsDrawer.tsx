import * as React from "react";
import {
  Drawer,
  DrawerContent,
  DrawerHandle,
  DrawerTitle,
  DrawerDescription,
} from "@/components/ui/drawer";

interface CandidateData {
  standard: Record<string, unknown>[];
  elevated: Record<string, unknown>[];
  stale: Record<string, unknown>[];
  superseded: Record<string, unknown>[];
  counts: { standard: number; elevated: number; stale: number; superseded: number };
}

interface DiagnosticsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  setCode: string;
}

export function DiagnosticsDrawer({ open, onOpenChange, setCode }: DiagnosticsDrawerProps) {
  const [data, setData] = React.useState<CandidateData | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetch(`/api/dev/candidate-queue?card_code_prefix=${setCode}-`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((d: CandidateData) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [open, setCode]);

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="max-h-[70dvh]">
        <DrawerHandle className="mx-auto mt-2 h-1 w-8 rounded-full bg-zinc-600" />
        <div className="overflow-y-auto px-4 pb-[max(16px,env(safe-area-inset-bottom,0px))] pt-3">
          <DrawerTitle className="text-[13px] font-bold text-drh-text">
            Diagnostics — {setCode}
          </DrawerTitle>
          <DrawerDescription className="sr-only">
            Candidate queue history and reconciliation status.
          </DrawerDescription>

          {loading && (
            <div className="mt-4 flex items-center gap-2 text-[12px] text-drh-muted">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
              Loading...
            </div>
          )}

          {data && !loading && (
            <div className="mt-3 space-y-3">
              {/* Counts summary */}
              <div className="grid grid-cols-4 gap-2">
                {(["standard", "elevated", "stale", "superseded"] as const).map((bucket) => (
                  <div
                    key={bucket}
                    className="rounded-md bg-drh-surface p-2 text-center ring-1 ring-white/[0.04]"
                  >
                    <div className="text-[16px] font-bold text-drh-text">
                      {data.counts[bucket]}
                    </div>
                    <div className="text-[9px] uppercase tracking-wide text-drh-muted">
                      {bucket}
                    </div>
                  </div>
                ))}
              </div>

              {/* Elevated items (most important) */}
              {data.elevated.length > 0 && (
                <div>
                  <h4 className="text-[11px] font-bold uppercase tracking-wide text-red-400">
                    Elevated ({data.elevated.length})
                  </h4>
                  <div className="mt-1 space-y-1">
                    {data.elevated.slice(0, 10).map((c, i) => (
                      <div
                        key={i}
                        className="rounded-md bg-red-500/5 px-2 py-1.5 text-[11px] text-drh-text ring-1 ring-red-500/10"
                      >
                        <span className="font-semibold text-amber-400">
                          {(c as { card_code?: string }).card_code || "—"}
                        </span>
                        {" — "}
                        {(c as { elevation_reason?: string }).elevation_reason || "Requires review"}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Standard pending */}
              {data.standard.length > 0 && (
                <div>
                  <h4 className="text-[11px] font-bold uppercase tracking-wide text-drh-muted">
                    Pending ({data.standard.length})
                  </h4>
                  <p className="mt-0.5 text-[11px] text-drh-muted/70">
                    {data.standard.length} candidates awaiting review.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
