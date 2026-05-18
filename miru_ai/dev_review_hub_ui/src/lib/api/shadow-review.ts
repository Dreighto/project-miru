/**
 * Shadow-review API client (PRO-909 PR-B).
 *
 * Backed by Flask routes in miru_ai/server.py:
 *   GET  /api/shadow-review/queue?limit=N
 *   GET  /api/shadow-review/item/<canonical_code>/<print_id>?contributing_model=<m>
 *   POST /api/shadow-review/verdict
 *
 * Server-side spec lives in miru_ai/shadow_review.py and matches these
 * TypeScript interfaces verbatim.
 */

export interface QueueItem {
  canonical_code: string;
  print_id: string;
  contributing_model: string;
  promotion_status: "experimental" | "review-ready" | "promoted" | "rejected";
  confidence_score: number;
  inconclusive_field_count: number;
  created_at: string;
  last_verified: string | null;
}

export interface QueueResponse {
  items: QueueItem[];
  total: number;
}

export type FieldTier = "hard" | "soft" | "inferred";
export type FieldOutcomeKind = "verified-correct" | "verified-wrong" | "inconclusive";

export interface FieldOutcome {
  field: string;
  tier: FieldTier;
  outcome: FieldOutcomeKind;
  reason: string;
  primary_value: unknown;
  validator_value: unknown | null;
  catalog_value: unknown;
  bandai_value: unknown | null;
}

export interface EvidenceItem {
  canonical_code: string;
  print_id: string;
  contributing_model: string;
  promotion_status: string;
  confidence_score: number;
  field_outcomes: FieldOutcome[];
  bandai_url: string | null;
  tcgplayer_url: string | null;
}

export type Verdict = "correct" | "wrong" | "defer";

export interface VerdictRequest {
  canonical_code: string;
  print_id: string;
  contributing_model: string;
  verdict: Verdict;
  sources_checked: string[];
  operator?: string;
}

export interface VerdictResponse {
  ok: boolean;
  new_promotion_status: string;
  event_logged: boolean;
}

export class ShadowReviewApiError extends Error {
  status: number;
  payload: unknown;
  constructor(status: number, message: string, payload: unknown = null) {
    super(message);
    this.name = "ShadowReviewApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    credentials: "same-origin",
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let payload: unknown = null;
    try {
      payload = await res.json();
    } catch {
      /* ignore */
    }
    throw new ShadowReviewApiError(res.status, `${res.status} ${res.statusText}`, payload);
  }
  return (await res.json()) as T;
}

export function fetchQueue(limit = 50): Promise<QueueResponse> {
  return _json<QueueResponse>(`/api/shadow-review/queue?limit=${encodeURIComponent(limit)}`);
}

export function fetchItem(
  canonicalCode: string,
  printId: string,
  contributingModel: string,
): Promise<EvidenceItem> {
  const params = new URLSearchParams({ contributing_model: contributingModel });
  return _json<EvidenceItem>(
    `/api/shadow-review/item/${encodeURIComponent(canonicalCode)}/${encodeURIComponent(printId)}?${params}`,
  );
}

export function submitVerdict(req: VerdictRequest): Promise<VerdictResponse> {
  return _json<VerdictResponse>(`/api/shadow-review/verdict`, {
    method: "POST",
    body: JSON.stringify(req),
  });
}
