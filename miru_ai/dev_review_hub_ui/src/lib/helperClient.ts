/**
 * Gemma 4 local helper API client.
 * Advisory-only — outputs never override governance.
 */

export interface HelperStatusResponse {
  /** Effective lane on (env + session override). */
  enabled: boolean;
  /** True when Ollama (or configured base) responds at /api/tags. */
  reachable?: boolean;
  /** MIRU_HELPER_ENABLED at process start. */
  envEnabled?: boolean;
  /** Session override active (POST /api/dev/helper/lane). */
  runtimeOverride?: boolean;
  model?: string;
  base_url?: string;
  error?: string;
}

export interface HelperInvokeResponse {
  ok: boolean;
  result?: string;
  error?: string;
}

export async function fetchHelperStatus(): Promise<HelperStatusResponse> {
  try {
    const res = await fetch("/api/dev/helper/status", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return { enabled: false };
    return (await res.json()) as HelperStatusResponse;
  } catch {
    return { enabled: false };
  }
}

export async function setHelperLane(
  enabled: boolean,
): Promise<HelperStatusResponse | null> {
  try {
    const res = await fetch("/api/dev/helper/lane", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "miru-client-nav",
      },
      credentials: "same-origin",
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) return null;
    return (await res.json()) as HelperStatusResponse;
  } catch {
    return null;
  }
}

export async function resetHelperLane(): Promise<HelperStatusResponse | null> {
  try {
    const res = await fetch("/api/dev/helper/lane", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "miru-client-nav",
      },
      credentials: "same-origin",
      body: JSON.stringify({ reset: true }),
    });
    if (!res.ok) return null;
    return (await res.json()) as HelperStatusResponse;
  } catch {
    return null;
  }
}

function pickAssistantText(data: Record<string, unknown>): string | undefined {
  for (const k of ["result", "summary", "explanation", "draft"]) {
    const v = data[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return undefined;
}

export async function invokeHelper(
  task: string,
  params: Record<string, string>,
): Promise<HelperInvokeResponse> {
  try {
    const res = await fetch("/api/dev/helper/invoke", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "miru-client-nav",
      },
      credentials: "same-origin",
      body: JSON.stringify({ task, params }),
    });
    const raw = (await res.json()) as Record<string, unknown>;
    const text = pickAssistantText(raw);
    const okFlag = Boolean(raw.ok);
    if (!res.ok) {
      return {
        ok: false,
        error:
          (typeof raw.error === "string" && raw.error) ||
          text ||
          `HTTP ${res.status}`,
      };
    }
    if (!okFlag) {
      return {
        ok: false,
        error: text || "Helper returned no result.",
      };
    }
    if (!text) {
      return { ok: false, error: "Helper returned no result." };
    }
    return { ok: true, result: text };
  } catch {
    return { ok: false, error: "Helper request failed." };
  }
}
