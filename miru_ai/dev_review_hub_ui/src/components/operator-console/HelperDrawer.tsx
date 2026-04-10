import * as React from "react";
import {
  Drawer,
  DrawerContent,
  DrawerHandle,
  DrawerTitle,
  DrawerDescription,
} from "@/components/ui/drawer";
import { Button } from "@/components/ui/button";
import {
  invokeHelper,
  resetHelperLane,
  setHelperLane,
  type HelperStatusResponse,
} from "@/lib/helperClient";
import { cn } from "@/lib/utils";
import type { OperatorHelperStatus } from "./MissionHeader";

interface HelperDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  topCardCode: string | null;
  helperStatus: OperatorHelperStatus;
  onHelperStatusUpdated: (hs: HelperStatusResponse) => void;
}

export function HelperDrawer({
  open,
  onOpenChange,
  topCardCode,
  helperStatus,
  onHelperStatusUpdated,
}: HelperDrawerProps) {
  const [rationale, setRationale] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [lanePending, setLanePending] = React.useState(false);
  const lastFetched = React.useRef<string | null>(null);

  React.useEffect(() => {
    lastFetched.current = null;
  }, [helperStatus.enabled, helperStatus.reachable, topCardCode]);

  React.useEffect(() => {
    if (!open || !topCardCode || topCardCode === lastFetched.current) return;
    lastFetched.current = topCardCode;
    setLoading(true);
    setError(null);
    setRationale(null);
    if (!helperStatus.enabled) {
      setLoading(false);
      setError("Helper lane is off. Turn it on above, then try again.");
      return;
    }
    if (!helperStatus.reachable) {
      setLoading(false);
      setError(
        "The helper lane is on, but the model server did not respond. Start your local LLM (e.g. Ollama) and try again.",
      );
      return;
    }
    invokeHelper("summarize_candidate", { card_code: topCardCode }).then((res) => {
      setLoading(false);
      if (res.ok && res.result) {
        setRationale(res.result);
      } else {
        setError(
          res.error ||
            "No advisory text returned. The model may have declined or timed out.",
        );
      }
    });
  }, [open, topCardCode, helperStatus.enabled, helperStatus.reachable]);

  React.useEffect(() => {
    if (!open) {
      lastFetched.current = null;
    }
  }, [open]);

  const applyLane = React.useCallback(
    async (fn: () => Promise<HelperStatusResponse | null>) => {
      setLanePending(true);
      const hs = await fn();
      setLanePending(false);
      if (hs) onHelperStatusUpdated(hs);
    },
    [onHelperStatusUpdated],
  );

  const onToggleLane = (next: boolean) => {
    void applyLane(() => setHelperLane(next));
  };

  const onResetLane = () => {
    void applyLane(() => resetHelperLane());
  };

  const oneLineStatus = !helperStatus.enabled
    ? "Lane off"
    : !helperStatus.reachable
      ? "Lane on — model unavailable"
      : "Lane on — model reachable";

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="max-h-[min(72dvh,100dvh)]">
        <DrawerHandle className="mx-auto mt-2 h-1 w-8 rounded-full bg-zinc-600" />
        <div className="px-4 pb-[max(16px,env(safe-area-inset-bottom,0px))] pt-3">
          <DrawerTitle className="text-[13px] font-bold text-amber-400">
            Helper — {topCardCode || "No card"}
          </DrawerTitle>
          <DrawerDescription className="mt-1 text-[11px] leading-snug text-drh-muted">
            Advisory summary for the top queue card. Does not override evidence
            or governance.
          </DrawerDescription>

          <div className="mt-3 flex flex-col gap-3 rounded-lg border border-white/[0.06] bg-zinc-950/40 p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[12px] font-medium text-drh-text">
                Helper lane
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={helperStatus.enabled}
                aria-busy={lanePending}
                disabled={lanePending}
                onClick={() => onToggleLane(!helperStatus.enabled)}
                className={cn(
                  "relative h-7 w-12 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70",
                  helperStatus.enabled ? "bg-emerald-600/85" : "bg-zinc-600",
                  lanePending && "opacity-60",
                )}
              >
                <span
                  className={cn(
                    "absolute top-0.5 left-0.5 h-6 w-6 rounded-full bg-white shadow transition-transform duration-200 ease-out",
                    helperStatus.enabled ? "translate-x-[1.25rem]" : "translate-x-0",
                  )}
                />
              </button>
            </div>
            {helperStatus.runtimeOverride ? (
              <Button
                type="button"
                variant="ghost"
                className="h-8 self-start px-2 text-[11px] text-drh-muted"
                disabled={lanePending}
                onClick={onResetLane}
              >
                Use environment default
              </Button>
            ) : null}
            <p className="text-[11px] leading-snug text-drh-muted">{oneLineStatus}</p>
            <details className="text-[10px] leading-snug text-drh-muted/80">
              <summary className="cursor-pointer select-none text-drh-muted">
                Technical details
              </summary>
              <ul className="mt-1.5 list-inside list-disc space-y-0.5">
                <li>
                  Environment default:{" "}
                  {helperStatus.envEnabled === true
                    ? "on"
                    : helperStatus.envEnabled === false
                      ? "off"
                      : "—"}
                </li>
                {helperStatus.runtimeOverride ? (
                  <li>Session override active for this Miru process.</li>
                ) : null}
                {helperStatus.model ? <li>Model: {helperStatus.model}</li> : null}
              </ul>
            </details>
          </div>

          <div className="mt-4 min-h-[72px]">
            {loading && (
              <div className="flex items-center gap-2 text-[12px] text-drh-muted">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
                Requesting summary…
              </div>
            )}
            {error && (
              <p className="text-[12px] leading-relaxed text-amber-400/95">
                {error}
              </p>
            )}
            {rationale && (
              <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-drh-text">
                {rationale}
              </p>
            )}
          </div>

          <p className="mt-4 border-t border-white/[0.06] pt-2 text-[9px] uppercase tracking-wide text-drh-muted/60">
            Generated by Miru local helper — advisory only, not authoritative
          </p>
        </div>
      </DrawerContent>
    </Drawer>
  );
}
