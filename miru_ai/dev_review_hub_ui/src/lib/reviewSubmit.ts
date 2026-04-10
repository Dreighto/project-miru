/**
 * Dev / training review submit — persists to Miru SQLite (18765).
 */

export type VerdictId = "looks_correct" | "needs_review" | "not_sure";

export type ReviewBarAction = "approve" | "fix_it" | "hold";

export interface CorrectionDetail {
  issue: string;
  target_table?: string;
  target_column?: string;
  target_row_id?: string;
  old_value?: string;
  new_value?: string;
  target_image?: string;
  corrected_market_id?: string;
  notes?: string;
}

export interface DevReviewHubSubmitPayload {
  cardId: string;
  variantId: string;
  variantKey?: string;
  verdict: VerdictId;
  issues: string[];
  because: string;
  source: string;
  action: ReviewBarAction;
  miruAssetsRelPath?: string | null;
  missingImageSourceUrl?: string;
  missingImageUploadName?: string;
  correctionDetail?: CorrectionDetail[];
}

export type ValidationResult =
  | { ok: true }
  | { ok: false; reason: string };

export function validateApprove(
  verdict: VerdictId | "",
  issues: string[],
  because: string,
  source: string,
): ValidationResult {
  if (!verdict) {
    return { ok: false, reason: "Select a verdict before approving." };
  }
  if (verdict === "looks_correct") {
    return { ok: true };
  }
  if (issues.length === 0) {
    return { ok: false, reason: "Select at least one issue." };
  }
  if (!because.trim()) {
    return { ok: false, reason: "Because is required for this verdict." };
  }
  if (!source.trim()) {
    return { ok: false, reason: "Source is required for this verdict." };
  }
  return { ok: true };
}

export function validateFixIt(
  verdict: VerdictId | "",
  issues: string[],
  because: string,
  source: string,
): ValidationResult {
  if (!verdict) {
    return { ok: false, reason: "Select a verdict before Fix it." };
  }
  if (verdict === "looks_correct") {
    return {
      ok: false,
      reason: "Fix it is not available when verdict is Looks correct.",
    };
  }
  if (issues.length === 0) {
    return { ok: false, reason: "Select at least one issue." };
  }
  if (!because.trim()) {
    return { ok: false, reason: "Because is required for Fix it." };
  }
  if (!source.trim()) {
    return { ok: false, reason: "Source is required for Fix it." };
  }
  return { ok: true };
}

export function validateHold(verdict: VerdictId | ""): ValidationResult {
  if (!verdict) {
    return { ok: false, reason: "Select a verdict before Hold." };
  }
  return { ok: true };
}

export async function submitDevTrainingReview(
  payload: DevReviewHubSubmitPayload,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const res = await fetch("/api/dev/training-review/submit", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Requested-With": "miru-client-nav",
    },
    credentials: "same-origin",
    body: JSON.stringify({
      cardId: payload.cardId,
      variantId: payload.variantId,
      variantKey: payload.variantKey ?? "",
      verdict: payload.verdict,
      issues: payload.issues,
      because: payload.because,
      source: payload.source,
      action: payload.action,
      miruAssetsRelPath: payload.miruAssetsRelPath ?? "",
      missingImageSourceUrl: payload.missingImageSourceUrl ?? "",
      missingImageUploadName: payload.missingImageUploadName ?? "",
      correctionDetail: payload.correctionDetail ?? [],
    }),
  });
  const data = (await res.json().catch(() => ({}))) as {
    ok?: boolean;
    error?: string;
  };
  if (!res.ok || data.ok === false) {
    return {
      ok: false,
      error: data.error || `Submit failed (${res.status})`,
    };
  }
  return { ok: true };
}
