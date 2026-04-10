import * as React from "react";

interface LegendData {
  badges: { key: string; label: string; description: string; color: string }[];
  states: { key: string; label: string; description: string }[];
  evidenceSources: { key: string; label: string; weight: number; canContradict: boolean }[];
  verdicts: { key: string; label: string; description: string }[];
}

interface OperatorLegendModalProps {
  open: boolean;
  onClose: () => void;
}

export function OperatorLegendModal({ open, onClose }: OperatorLegendModalProps) {
  const [data, setData] = React.useState<LegendData | null>(null);

  React.useEffect(() => {
    if (!open) return;
    fetch("/api/dev/operator-console/legend", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((d: LegendData) => setData(d))
      .catch(() => {});
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-y-auto bg-black/80 p-4 pb-[max(16px,env(safe-area-inset-bottom,0px))] pt-[max(16px,env(safe-area-inset-top,0px))]"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-[400px] rounded-xl bg-drh-bg p-4 shadow-2xl ring-1 ring-white/[0.08]">
        <div className="flex items-center justify-between">
          <h2 className="text-[14px] font-bold text-drh-text">Operator Legend</h2>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-full text-drh-muted transition-colors hover:bg-white/[0.06] hover:text-drh-text"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </button>
        </div>

        {!data ? (
          <div className="mt-4 flex justify-center">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
          </div>
        ) : (
          <div className="mt-3 space-y-4">
            {/* Badges */}
            <section>
              <h3 className="text-[11px] font-bold uppercase tracking-wide text-amber-400">
                Issue Badges
              </h3>
              <div className="mt-1.5 space-y-1">
                {data.badges.map((b) => (
                  <div key={b.key} className="flex gap-2 text-[11px]">
                    <span className="shrink-0 font-semibold" style={{ color: b.color }}>
                      {b.label}
                    </span>
                    <span className="text-drh-muted">{b.description}</span>
                  </div>
                ))}
              </div>
            </section>

            {/* States */}
            <section>
              <h3 className="text-[11px] font-bold uppercase tracking-wide text-amber-400">
                Card States
              </h3>
              <div className="mt-1.5 space-y-1">
                {data.states.map((s) => (
                  <div key={s.key} className="flex gap-2 text-[11px]">
                    <span className="shrink-0 font-semibold text-drh-text">{s.label}</span>
                    <span className="text-drh-muted">{s.description}</span>
                  </div>
                ))}
              </div>
            </section>

            {/* Evidence sources */}
            <section>
              <h3 className="text-[11px] font-bold uppercase tracking-wide text-amber-400">
                Evidence Sources
              </h3>
              <div className="mt-1.5 space-y-1">
                {data.evidenceSources.map((e) => (
                  <div key={e.key} className="flex items-center gap-2 text-[11px]">
                    <span className="shrink-0 font-semibold text-drh-text">{e.label}</span>
                    <span className="text-drh-muted">w:{e.weight}</span>
                    {e.canContradict && (
                      <span className="rounded-full bg-red-500/15 px-1.5 py-0.5 text-[8px] font-semibold text-red-400">
                        can contradict
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </section>

            {/* Verdicts */}
            <section>
              <h3 className="text-[11px] font-bold uppercase tracking-wide text-amber-400">
                Verdicts
              </h3>
              <div className="mt-1.5 space-y-1">
                {data.verdicts.map((v) => (
                  <div key={v.key} className="flex gap-2 text-[11px]">
                    <span className="shrink-0 font-semibold text-drh-text">{v.label}</span>
                    <span className="text-drh-muted">{v.description}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
