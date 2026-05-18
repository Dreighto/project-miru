import type { EvidenceItem, FieldOutcome } from "@/lib/api/shadow-review";

interface Props {
  item: EvidenceItem;
  children?: React.ReactNode;
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "(empty)";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function outcomeClass(outcome: FieldOutcome["outcome"]): string {
  switch (outcome) {
    case "verified-correct":
      return "text-emerald-300";
    case "verified-wrong":
      return "text-red-300";
    case "inconclusive":
      return "text-zinc-400";
  }
}

function tierClass(tier: FieldOutcome["tier"]): string {
  switch (tier) {
    case "hard":
      return "text-zinc-300";
    case "soft":
      return "text-zinc-400";
    case "inferred":
      return "text-zinc-500 italic";
  }
}

export function EvidencePanel({ item, children }: Props) {
  return (
    <section
      aria-label="Evidence panel"
      className="flex flex-col gap-3 rounded-md border border-zinc-800 bg-zinc-950/40 p-4"
    >
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <h2 className="font-mono text-sm text-zinc-100">
            {item.canonical_code}
            {item.print_id !== item.canonical_code ? ` · ${item.print_id}` : ""}
          </h2>
          <div className="mt-0.5 text-[11px] text-zinc-500">
            <span className="font-mono">{item.contributing_model}</span>
            {" · "}
            <span>{item.promotion_status}</span>
            {" · "}
            <span>
              confidence <span className="font-mono">{item.confidence_score.toFixed(3)}</span>
            </span>
          </div>
        </div>
        <div className="flex gap-2 text-[11px]">
          {item.bandai_url && (
            <a
              href={item.bandai_url}
              target="_blank"
              rel="noreferrer noopener"
              className="rounded border border-zinc-700 bg-zinc-900/60 px-2 py-1 text-zinc-300 hover:border-amber-300/60 hover:text-amber-200"
            >
              Bandai ↗
            </a>
          )}
          {item.tcgplayer_url && (
            <a
              href={item.tcgplayer_url}
              target="_blank"
              rel="noreferrer noopener"
              className="rounded border border-zinc-700 bg-zinc-900/60 px-2 py-1 text-zinc-300 hover:border-amber-300/60 hover:text-amber-200"
            >
              TCGPlayer ↗
            </a>
          )}
        </div>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-[10px] uppercase tracking-wider text-zinc-500">
              <th className="px-2 py-1 font-medium">Field</th>
              <th className="px-2 py-1 font-medium">Primary</th>
              <th className="px-2 py-1 font-medium">Validator</th>
              <th className="px-2 py-1 font-medium">Catalog</th>
              <th className="px-2 py-1 font-medium">Bandai</th>
              <th className="px-2 py-1 font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {item.field_outcomes.map((f) => (
              <tr key={f.field} className="border-b border-zinc-900/60 last:border-b-0">
                <td className={`px-2 py-1 font-mono ${tierClass(f.tier)}`}>{f.field}</td>
                <td className="px-2 py-1 font-mono text-zinc-300">{renderValue(f.primary_value)}</td>
                <td className="px-2 py-1 font-mono text-zinc-400">
                  {renderValue(f.validator_value)}
                </td>
                <td className="px-2 py-1 font-mono text-zinc-200">{renderValue(f.catalog_value)}</td>
                <td className="px-2 py-1 font-mono text-zinc-400">{renderValue(f.bandai_value)}</td>
                <td className={`px-2 py-1 font-mono text-[11px] ${outcomeClass(f.outcome)}`}>
                  {f.outcome}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Verifier reasoning trail */}
      <details className="rounded border border-zinc-800/60 bg-zinc-900/40 px-3 py-2 text-[11px] text-zinc-400">
        <summary className="cursor-pointer text-zinc-300">Verifier reasoning trail</summary>
        <ul className="mt-2 space-y-1 font-mono">
          {item.field_outcomes.map((f) => (
            <li key={f.field}>
              <span className="text-zinc-500">{f.field}:</span>{" "}
              <span className={outcomeClass(f.outcome)}>{f.outcome}</span>
              {f.reason ? <span className="text-zinc-400"> — {f.reason}</span> : null}
            </li>
          ))}
        </ul>
      </details>

      {children}
    </section>
  );
}
