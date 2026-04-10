import { cn } from "@/lib/utils";
import {
  miruEyebrowClass,
  miruScopeBadgeClass,
  miruSupportingClass,
} from "@/components/shell/MiruPageHeader";

export interface OperatorHelperStatus {
  enabled: boolean;
  reachable: boolean;
  model?: string;
  envEnabled?: boolean;
  runtimeOverride?: boolean;
}

interface MissionHeaderProps {
  setCode: string;
  counts: { pending: number; elevated: number; stale: number };
  helperStatus: OperatorHelperStatus;
  onInfoClick: () => void;
  onHelperClick: () => void;
  onDiagnosticsClick: () => void;
}

/** Compact label for header — no env-var dump. */
function helperPill(s: OperatorHelperStatus): {
  dotClass: string;
  pill: string;
  title: string;
} {
  if (!s.enabled) {
    return {
      dotClass: "bg-zinc-500",
      pill: "Off",
      title: "Helper lane off. Open for controls.",
    };
  }
  if (!s.reachable) {
    return {
      dotClass: "bg-amber-500",
      pill: "No LLM",
      title: "Helper on, but the local model server did not respond.",
    };
  }
  return {
    dotClass: "bg-emerald-400",
    pill: "Ready",
    title: "Helper ready. Open for an advisory summary.",
  };
}

export function MissionHeader({
  setCode,
  counts,
  helperStatus,
  onInfoClick,
  onHelperClick,
  onDiagnosticsClick,
}: MissionHeaderProps) {
  const hp = helperPill(helperStatus);
  return (
    <header className="mission-header sticky top-0 z-20 border-b border-drh-stroke/60 bg-drh-bg/95 px-4 pb-3 pt-2 backdrop-blur-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className={miruEyebrowClass}>Operator</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-bold leading-tight tracking-tight text-[#c9a84c] sm:text-xl">
              Operator Console
            </h1>
            <span className={miruScopeBadgeClass}>Current scope: {setCode}</span>
          </div>
          <p className={`${miruSupportingClass} mt-1 max-w-[20rem]`}>
            Bounded queue — adjudicate the next card.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-drh-muted">
            <span>
              P:<span className="font-semibold text-drh-text">{counts.pending}</span>
            </span>
            {counts.elevated > 0 && (
              <span className="text-red-400">
                E:<span className="font-semibold">{counts.elevated}</span>
              </span>
            )}
            {counts.stale > 0 && (
              <span>
                S:<span className="font-semibold text-drh-text">{counts.stale}</span>
              </span>
            )}
          </div>
        </div>

        <div className="flex max-w-[min(100%,11rem)] shrink-0 flex-wrap items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onHelperClick}
            className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border border-white/[0.08] bg-zinc-950/50 py-0.5 pl-1.5 pr-2 transition-colors hover:bg-zinc-900/60"
            title={hp.title}
            aria-label={`Helper: ${hp.pill}`}
          >
            <span
              className={cn("h-1.5 w-1.5 shrink-0 rounded-full", hp.dotClass)}
            />
            <span className="truncate text-[10px] font-semibold uppercase tracking-wide text-drh-muted">
              {hp.pill}
            </span>
          </button>
          <button
            type="button"
            onClick={onDiagnosticsClick}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[14px] text-drh-muted transition-colors hover:bg-white/[0.06] hover:text-drh-text"
            aria-label="Diagnostics"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
          </button>
          <button
            type="button"
            onClick={onInfoClick}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[14px] text-drh-muted transition-colors hover:bg-white/[0.06] hover:text-drh-text"
            aria-label="Operator legend"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
          </button>
        </div>
      </div>
    </header>
  );
}
