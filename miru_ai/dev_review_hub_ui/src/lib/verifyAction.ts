/**
 * Verify-action pre-flight governance gate.
 * Calls the backend to check if an action is permissible before commit.
 */

export interface VerifyActionRequest {
  cardId: string;
  variantId: string;
  action: "approve" | "fix_it" | "hold";
}

export interface VerifyActionResponse {
  ok: boolean;
  conflict?: string;
  banner?: string;
}

export async function verifyAction(
  req: VerifyActionRequest,
): Promise<VerifyActionResponse> {
  const res = await fetch("/api/dev/training-review/verify-action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Requested-With": "miru-client-nav",
    },
    credentials: "same-origin",
    body: JSON.stringify({
      cardId: req.cardId,
      variantId: req.variantId,
      action: req.action,
    }),
  });
  const data = (await res.json().catch(() => ({}))) as VerifyActionResponse;
  if (!res.ok) {
    return {
      ok: false,
      conflict: "network_error",
      banner: `Verification failed (HTTP ${res.status})`,
    };
  }
  return data;
}
