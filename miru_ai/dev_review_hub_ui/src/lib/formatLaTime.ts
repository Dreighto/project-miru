/** Display timestamps in America/Los_Angeles for operator-facing review history. */
const LA: Intl.DateTimeFormatOptions = {
  timeZone: "America/Los_Angeles",
  dateStyle: "medium",
  timeStyle: "short",
};

export function formatLosAngelesDateTime(isoOrRaw: string): string {
  const s = String(isoOrRaw || "").trim();
  if (!s) return "—";
  let d: Date;
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(s)) {
    const normalized = s.includes("T") ? s : s.replace(" ", "T");
    d = new Date(
      normalized.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(normalized)
        ? normalized
        : `${normalized}Z`,
    );
  } else {
    d = new Date(s);
  }
  if (Number.isNaN(d.getTime())) return s;
  return new Intl.DateTimeFormat("en-US", LA).format(d);
}
