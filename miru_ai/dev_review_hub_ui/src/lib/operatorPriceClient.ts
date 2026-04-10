/**
 * Operator review price context — catalog snapshot + narrow TCGCSV refresh (18765).
 */

export interface OperatorPriceMapping {
  truth: "confirmed" | "weak" | "unmapped" | "unresolved";
  reason?: string;
  confidence?: string;
  method?: string;
  market_product_pk?: number;
}

export interface OperatorMarketProduct {
  id: string;
  productName: string;
  marketVariantLabel: string;
  source: string;
}

export interface OperatorPriceRow {
  market: number | null;
  mid: number | null;
  low: number | null;
  subtypeName: string | null;
  sourceName: string;
  capturedAtIso: string;
}

export interface OperatorPriceSnapshot {
  ok: boolean;
  error?: string;
  printingId?: number;
  cardCode?: string;
  variantKey?: string;
  variantLabel?: string;
  mapping?: OperatorPriceMapping;
  marketProduct?: OperatorMarketProduct;
  price?: OperatorPriceRow | null;
  freshness?: string;
  refreshed?: boolean;
  previous?: OperatorPriceSnapshot;
}

export async function fetchOperatorPriceContext(params: {
  printingId: number;
  cardCode: string;
  variantKey: string;
}): Promise<OperatorPriceSnapshot> {
  const q = new URLSearchParams({
    printing_id: String(params.printingId),
    card_code: params.cardCode,
    variant_key: params.variantKey,
  });
  try {
    const res = await fetch(`/api/dev/operator/price-context?${q}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const data = (await res.json()) as OperatorPriceSnapshot;
    return data;
  } catch {
    return { ok: false, error: "Price context request failed." };
  }
}

export async function refreshOperatorPrice(params: {
  printingId: number;
  cardCode: string;
  variantKey: string;
}): Promise<{ ok: boolean; snapshot?: OperatorPriceSnapshot; error?: string }> {
  try {
    const res = await fetch("/api/dev/operator/price-refresh", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "miru-client-nav",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        printing_id: params.printingId,
        card_code: params.cardCode,
        variant_key: params.variantKey,
      }),
    });
    const data = (await res.json()) as OperatorPriceSnapshot & {
      previous?: OperatorPriceSnapshot;
      error?: string;
    };
    if (!res.ok || !data.ok) {
      return {
        ok: false,
        error: data.error || `HTTP ${res.status}`,
        snapshot: data.previous,
      };
    }
    return { ok: true, snapshot: data };
  } catch {
    return { ok: false, error: "Price refresh request failed." };
  }
}
