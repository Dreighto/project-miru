import type { VerdictId } from "@/lib/reviewSubmit";

export type VerdictOption = VerdictId;

export interface VariantDef {
  id: string;
  label: string;
  variantKey?: string;
  /** Absent, null, or empty = no verified on-disk image for this variant. */
  imageUrl?: string | null;
  miruAssetsRelPath?: string | null;
}

export interface HistoryEntry {
  id: string;
  title: string;
  body: string;
}

export function pickInitialVariant(
  variants: VariantDef[],
  versionHint: string,
): string {
  if (!variants.length) return "";
  const hint = versionHint.trim().toLowerCase();
  const match = variants.find((v) => {
    const id = v.id.toLowerCase();
    const lab = v.label.toLowerCase().replace(/\s+/g, "");
    const h = hint.replace(/\s+/g, "");
    return id === hint || lab === h || id === h || lab === hint;
  });
  return match?.id ?? variants[0].id;
}

export function getVariantImageUrl(
  mock: { variants: VariantDef[] },
  variantId: string,
): string | null {
  const v = mock.variants.find((x) => x.id === variantId);
  const u = v?.imageUrl;
  if (u == null || String(u).trim() === "") {
    return null;
  }
  return String(u).trim();
}

export const VERDICT_OPTIONS: { value: VerdictOption; label: string }[] = [
  { value: "looks_correct", label: "Looks correct" },
  { value: "needs_review", label: "Needs review" },
  { value: "not_sure", label: "Not sure" },
];

export const ISSUE_CHIP_OPTIONS: { id: string; label: string }[] = [
  { id: "thumb_mismatch", label: "Thumb mismatch" },
  { id: "price_band", label: "Price band" },
  { id: "rules_text", label: "Rules text" },
  { id: "variant_code", label: "Variant code" },
  { id: "metadata", label: "Metadata" },
  { id: "other", label: "Other" },
];
