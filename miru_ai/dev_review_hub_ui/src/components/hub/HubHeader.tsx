import type { HubSummary } from "./types";
import { MiruPageHeaderCenter } from "@/components/shell/MiruPageHeader";

function statusLabel(data: HubSummary): { text: string; color: string } {
  if (data.health.issue_count > 0) return { text: "Degraded", color: "#f59e0b" };
  if (!data.catalog.usable) return { text: "Unknown", color: "#6b7280" };
  return { text: "Healthy", color: "#22c55e" };
}

export function HubHeader({ data }: { data: HubSummary }) {
  const status = statusLabel(data);
  return (
    <header className="flex flex-col items-center gap-4 px-4 pb-6 pt-8">
      <img
        src="/static/icons/miru-fruit.png"
        alt="Miru"
        className="h-14 w-14 drop-shadow-lg"
      />
      <MiruPageHeaderCenter
        eyebrow="Miru"
        title="Miru Hub"
        description="Live-backed overview from catalog, dossier, and pipeline DBs on this runtime."
        footer={
          <div className="mt-1 flex flex-wrap items-center justify-center gap-2.5">
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
              style={{
                background: `${status.color}18`,
                color: status.color,
              }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: status.color }}
              />
              {status.text}
            </span>
            <span className="font-mono text-xs text-drh-muted">:18765</span>
          </div>
        }
      />
    </header>
  );
}
