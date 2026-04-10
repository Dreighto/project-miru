import { MagicCard } from "@/components/magicui/magic-card";
import type { HubSummary } from "./types";

export function WhatsGoingOn({ data }: { data: HubSummary }) {
  const hasIssues = data.health.issue_count > 0;
  const hasReviews = data.throughput.today_reviews > 0;

  return (
    <section className="px-4">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-drh-muted mb-1">
        What's going on
      </h2>
      <p className="text-[10px] text-drh-muted/65 leading-snug mb-3">
        Runtime dependency checks plus the same OP01 throughput as Stats.
      </p>
      <MagicCard>
        <div className="space-y-2.5 text-sm">
          {hasIssues ? (
            data.health.issues.map((issue, i) => (
              <div
                key={i}
                className="flex items-start gap-2 text-amber-400/90"
              >
                <span className="shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full bg-amber-400" />
                <span>{issue}</span>
              </div>
            ))
          ) : (
            <div className="flex items-start gap-2 text-emerald-400/80">
              <span className="shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full bg-emerald-400" />
              <span>System running normally. No active issues.</span>
            </div>
          )}
          {hasReviews && (
            <div className="flex items-start gap-2 text-drh-text/70">
              <span className="shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full bg-[#c9a84c]" />
              <span>
                {data.throughput.today_reviews} review
                {data.throughput.today_reviews !== 1 ? "s" : ""} today
                {data.throughput.distinct_cards_reviewed > 0
                  ? ` across ${data.throughput.distinct_cards_reviewed} card${data.throughput.distinct_cards_reviewed !== 1 ? "s" : ""}`
                  : ""}
              </span>
            </div>
          )}
        </div>
      </MagicCard>
    </section>
  );
}
