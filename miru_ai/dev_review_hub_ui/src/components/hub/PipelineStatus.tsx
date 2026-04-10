import type { HubSummary } from "./types";

interface Stage {
  label: string;
  count: number;
  suffix: string;
}

export function PipelineStatus({ data }: { data: HubSummary }) {
  const stages: Stage[] = [
    { label: "Ingest", count: data.catalog.total_cards, suffix: "cards indexed" },
    { label: "Normalize", count: data.dossier.dossiers_created, suffix: "dossiers built" },
    { label: "Image Map", count: data.pipeline.image_variant_analysis_count, suffix: "variants mapped" },
    { label: "Pricing Bridge", count: data.pipeline.market_prices_count, suffix: "prices linked" },
    { label: "Certify", count: data.dossier.verified_dossiers, suffix: "verified" },
    { label: "Publish", count: data.pipeline.publication_stage_count, suffix: "staged" },
  ];

  return (
    <section className="px-4">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-drh-muted mb-1">
        Pipeline
      </h2>
      <p className="text-[10px] text-drh-muted/65 leading-snug mb-3">
        Stage counts use the same catalog, dossier, and pipeline sources as Coverage.
      </p>
      <div className="relative pl-5">
        {/* Vertical connector line */}
        <div className="absolute left-[7px] top-2 bottom-2 w-px bg-white/[0.08]" />
        <div className="space-y-3.5">
          {stages.map((s) => (
            <div key={s.label} className="relative flex items-start gap-3">
              <span
                className="absolute left-[-13px] top-[5px] w-2.5 h-2.5 rounded-full border-2 shrink-0"
                style={{
                  borderColor: s.count > 0 ? "#22c55e" : "#3f3f46",
                  background: s.count > 0 ? "#22c55e" : "transparent",
                }}
              />
              <div className="flex items-baseline gap-2 min-w-0">
                <span className="text-sm font-medium text-drh-text">{s.label}</span>
                <span className="text-xs text-drh-muted tabular-nums">
                  {s.count > 0
                    ? `${s.count.toLocaleString()} ${s.suffix}`
                    : "Pending"}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
