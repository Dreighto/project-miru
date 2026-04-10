import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { MagicCard } from "@/components/magicui/magic-card";
import { NumberTicker } from "@/components/magicui/number-ticker";
import { Progress } from "@/components/ui/progress";
import type { HubSummary, ResourceMetric } from "./types";

function ResourceBar({ r }: { r: ResourceMetric }) {
  if (!r.available) return null;
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-drh-muted w-14 shrink-0">{r.label}</span>
      <Progress
        value={r.percent}
        className="flex-1 h-1.5"
        indicatorColor="#c9a84c"
      />
      <span className="text-[11px] text-drh-muted tabular-nums w-16 text-right shrink-0">
        {r.value}
      </span>
    </div>
  );
}

export function BasicStats({ data }: { data: HubSummary }) {
  const available = data.resources.filter((r) => r.available);

  return (
    <section className="px-4 space-y-3">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-drh-muted mb-1">
        Stats
      </h2>
      <p className="text-[10px] text-drh-muted/65 leading-snug mb-3">
        OP01 training-review throughput (dev_training_reviews, OP01 card codes).
      </p>

      {/* Throughput */}
      <MagicCard>
        <div className="flex items-center justify-between gap-4">
          <div className="text-center flex-1">
            <NumberTicker
              value={data.throughput.today_reviews}
              className="text-xl font-bold text-[#c9a84c]"
            />
            <div className="text-[10px] text-drh-muted uppercase tracking-wide mt-0.5">
              Today
            </div>
            <div className="text-[9px] text-drh-muted/55 mt-0.5">Reviews today</div>
          </div>
          {/* Softer dividers */}
          <div className="w-px h-7 bg-drh-stroke/50" />
          <div className="text-center flex-1">
            <NumberTicker
              value={data.throughput.total_reviews}
              className="text-xl font-bold text-drh-text"
            />
            <div className="text-[10px] text-drh-muted uppercase tracking-wide mt-0.5">
              Total Reviews
            </div>
            <div className="text-[9px] text-drh-muted/55 mt-0.5">All OP01 review rows</div>
          </div>
          <div className="w-px h-7 bg-drh-stroke/50" />
          <div className="text-center flex-1">
            <NumberTicker
              value={data.throughput.distinct_cards_reviewed}
              className="text-xl font-bold text-[#a68bdb]"
            />
            <div className="text-[10px] text-drh-muted uppercase tracking-wide mt-0.5">
              Cards
            </div>
            <div className="text-[9px] text-drh-muted/55 mt-0.5">Distinct OP01 codes</div>
          </div>
        </div>
      </MagicCard>

      {/* Resources */}
      {available.length > 0 && (
        <MagicCard>
          <div className="space-y-3">
            {available.map((r) => (
              <ResourceBar key={r.key} r={r} />
            ))}
          </div>
        </MagicCard>
      )}

      {/* Health issues (expandable) */}
      {data.health.issue_count > 0 && (
        <Accordion type="single" collapsible>
          <AccordionItem value="issues" className="border-none">
            <AccordionTrigger className="text-sm text-amber-400/80 hover:no-underline py-2 px-1">
              {data.health.issue_count} issue
              {data.health.issue_count !== 1 ? "s" : ""} detected
            </AccordionTrigger>
            <AccordionContent>
              <div className="space-y-1.5 text-xs text-drh-muted px-1">
                {data.health.issues.map((issue, i) => (
                  <p key={i}>{issue}</p>
                ))}
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      )}
    </section>
  );
}
