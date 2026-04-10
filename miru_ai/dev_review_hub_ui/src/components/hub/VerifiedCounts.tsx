import { MagicCard } from "@/components/magicui/magic-card";
import { NumberTicker } from "@/components/magicui/number-ticker";
import { Progress } from "@/components/ui/progress";
import type { HubSummary } from "./types";

interface Tile {
  label: string;
  hint: string;
  value: number | null;
  denominator?: number;
  accent: string;
}

function TileCard({ tile }: { tile: Tile }) {
  const ratio =
    tile.denominator && tile.denominator > 0 && tile.value !== null
      ? Math.min((tile.value / tile.denominator) * 100, 100)
      : null;

  return (
    <MagicCard>
      <div className="flex flex-col gap-1.5">
        {/* Value with animated ticker */}
        {tile.value !== null ? (
          <span className="text-2xl font-bold" style={{ color: tile.accent }}>
            <NumberTicker value={tile.value} />
          </span>
        ) : (
          <span className="text-2xl font-bold text-drh-muted/50">—</span>
        )}

        {/* Label */}
        <span className="text-[11px] font-medium text-drh-muted uppercase tracking-wide leading-tight">
          {tile.label}
        </span>
        <span className="text-[9px] text-drh-muted/55 leading-snug">{tile.hint}</span>

        {/* Progress bar when denominator is known */}
        {ratio !== null && (
          <Progress
            value={ratio}
            className="mt-0.5 h-1"
            indicatorColor={tile.accent}
          />
        )}

        {/* Denominator label */}
        {tile.denominator !== undefined && tile.value !== null && (
          <span className="text-[10px] text-drh-muted/60 tabular-nums">
            of {tile.denominator.toLocaleString()}
          </span>
        )}
      </div>
    </MagicCard>
  );
}

export function VerifiedCounts({ data }: { data: HubSummary }) {
  const tiles: Tile[] = [
    {
      label: "Verified Cards",
      hint: "Dossier store: cards/dossiers marked verified.",
      value: data.dossier.verified_dossiers,
      denominator: data.catalog.total_cards,
      accent: "#22c55e",
    },
    {
      label: "Dossiers Built",
      hint: "Dossier store: total dossier records.",
      value: data.dossier.dossiers_created,
      denominator: data.catalog.total_cards,
      accent: "#a68bdb",
    },
    {
      label: "Image Mapping",
      hint: "Catalog: rows in image_variant_analysis.",
      value: data.pipeline.image_variant_analysis_count,
      denominator: data.catalog.total_variants,
      accent: "#60a5fa",
    },
    {
      label: "Pricing Bridge",
      hint: "Catalog: rows in market_prices.",
      value: data.pipeline.market_prices_count,
      denominator: data.catalog.total_variants,
      accent: "#c9a84c",
    },
    {
      label: "Pending Review",
      hint: "Catalog: rows in miru_review_queue.",
      value: data.pipeline.review_queue_count,
      accent: "#f59e0b",
    },
    {
      label: "Publication stage",
      hint: "Catalog: rows in miru_publication_stage (staged pipeline).",
      value: data.pipeline.publication_stage_count,
      accent: "#6c3fc4",
    },
  ];

  return (
    <section className="px-4">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-drh-muted mb-1">
        Coverage
      </h2>
      <p className="text-[10px] text-drh-muted/65 leading-snug mb-3">
        Denominators use catalog card/variant totals; dossier counts come from the dossier DB.
      </p>
      <div className="grid grid-cols-2 gap-2.5">
        {tiles.map((t) => (
          <TileCard key={t.label} tile={t} />
        ))}
      </div>
    </section>
  );
}
