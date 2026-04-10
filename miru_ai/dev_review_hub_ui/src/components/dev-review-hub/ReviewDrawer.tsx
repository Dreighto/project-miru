import * as React from "react";
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerHandle,
  DrawerTitle,
} from "@/components/ui/drawer";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MagicCard } from "@/components/magicui/magic-card";
import { ShimmerButton } from "@/components/magicui/shimmer-button";
import type { QueueItem } from "@/data/mockQueue";
import {
  ISSUE_CHIP_OPTIONS,
  VERDICT_OPTIONS,
  getVariantImageUrl,
  pickInitialVariant,
} from "@/data/mockReviewContent";
import {
  submitDevTrainingReview,
  validateApprove,
  validateFixIt,
  validateHold,
  type CorrectionDetail,
  type DevReviewHubSubmitPayload,
  type ReviewBarAction,
  type VerdictId,
} from "@/lib/reviewSubmit";
import { Skeleton } from "@/components/ui/skeleton";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { createPortal } from "react-dom";
import { formatLosAngelesDateTime } from "@/lib/formatLaTime";
import {
  fetchOperatorPriceContext,
  refreshOperatorPrice,
  type OperatorPriceSnapshot,
} from "@/lib/operatorPriceClient";
import { RefreshCw, X } from "lucide-react";

type HistoryRow = NonNullable<QueueItem["miruHistory"]>[number];

function formatHistoryWhen(entry: HistoryRow): string {
  if (entry.createdAtIso) {
    return formatLosAngelesDateTime(entry.createdAtIso);
  }
  const head = entry.title.split(" · ")[0]?.trim() ?? "";
  if (/^\d{4}-\d{2}-\d{2}/.test(head)) {
    return formatLosAngelesDateTime(head.replace(" ", "T") + "Z");
  }
  return head || "—";
}

function formatUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(n));
}

function freshnessPill(
  api: string | undefined,
  showFailed: boolean,
): { label: string; className: string } {
  if (showFailed) {
    return { label: "Failed", className: "bg-red-500/25 text-red-300" };
  }
  const f = api ?? "unknown";
  if (f === "fresh") {
    return { label: "Fresh", className: "bg-emerald-500/25 text-emerald-300/95" };
  }
  if (f === "aging") {
    return { label: "Aging", className: "bg-amber-500/20 text-amber-300/90" };
  }
  if (f === "stale") {
    return { label: "Stale", className: "bg-red-500/15 text-red-300/85" };
  }
  return { label: "Unknown", className: "bg-zinc-500/20 text-zinc-400" };
}

function ImageLightboxOverlay({
  open,
  imageUrl,
  label,
  onClose,
}: {
  open: boolean;
  imageUrl: string;
  label: string;
  onClose: () => void;
}) {
  // Scroll lock while open. IMPORTANT: do NOT set body `touch-action: none` or
  // `overscroll-behavior: none` — on iOS Safari those can interfere with the
  // touch → click synthesis and cause dead tap targets inside the lightbox.
  // Plain overflow lock is enough to keep the underlying drawer from scrolling.
  React.useEffect(() => {
    if (!open) return;
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    html.style.overflow = "hidden";
    body.style.overflow = "hidden";
    body.classList.add("drh-image-lightbox-open");
    return () => {
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
      body.classList.remove("drh-image-lightbox-open");
    };
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Capture + stop so Radix Dialog (Vaul drawer) does not treat Escape as "dismiss drawer".
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open || !imageUrl || typeof document === "undefined") return null;

  // iOS-native touch structure (no onPointerDown close — that causes ghost-click
  // through to the drawer beneath because the element is removed before the
  // synthesized click lands). Uses onClick on a cursor:pointer root so iOS
  // reliably dispatches click events on the backdrop. Image container has
  // pointer-events:none so taps over the image pass through to the root
  // backdrop. Close button is a real <button> with a 48px hit target.
  return createPortal(
    <div
      className="fixed inset-0 z-[100000] flex min-h-[100dvh] w-full max-w-[100vw] bg-black/95"
      style={{
        isolation: "isolate",
        touchAction: "manipulation",
        cursor: "pointer",
        WebkitTapHighlightColor: "transparent",
        // CRITICAL: Vaul sets `pointer-events: none` inline on <body> while the
        // drawer is open to block background interaction. Our portal is a
        // direct child of <body>, so it inherits `pointer-events: none` and
        // every tap is dead (X button dead, backdrop dead, taps fall through
        // to the drawer beneath). Explicitly re-enable pointer events here so
        // the lightbox receives real touch input on iPhone.
        pointerEvents: "auto",
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Enlarged card image"
      onClick={onClose}
    >
      {/* Image layer — pointer-events:none so taps fall through to the root backdrop */}
      <div
        className="pointer-events-none absolute inset-0 flex items-center justify-center"
        style={{
          paddingTop: "calc(env(safe-area-inset-top) + 4rem)",
          paddingBottom: "calc(env(safe-area-inset-bottom) + 1rem)",
          paddingLeft: "1rem",
          paddingRight: "1rem",
        }}
      >
        <img
          src={imageUrl}
          alt={label}
          className="max-h-full max-w-full object-contain"
          draggable={false}
        />
      </div>

      {/* Close button — real <button>, 48×48 hit target, stops propagation so
          the root backdrop handler doesn't also fire */}
      <button
        type="button"
        aria-label="Close image"
        className="absolute right-3 z-10 flex h-12 w-12 items-center justify-center rounded-full bg-black/70 text-white/95 shadow-lg active:bg-white/25"
        style={{
          top: "max(0.75rem, env(safe-area-inset-top))",
          cursor: "pointer",
          WebkitTapHighlightColor: "transparent",
          touchAction: "manipulation",
        }}
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      >
        <X className="h-6 w-6" />
      </button>
    </div>,
    document.body,
  );
}

export interface ReviewDrawerProps {
  item: QueueItem;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAnimationEnd?: (open: boolean) => void;
  onSubmitted?: () => void;
}

function buildCorrectionDetail(
  issues: string[],
  cardId: string,
  because: string,
  miruAssetsRelPath: string | null | undefined,
): CorrectionDetail[] {
  if (issues.length === 0) return [];
  const trimBecause = because.trim();
  return issues.map((issue) => {
    const detail: CorrectionDetail = { issue };
    if (cardId) detail.target_row_id = cardId;
    if (miruAssetsRelPath) detail.target_image = miruAssetsRelPath;
    if (trimBecause) detail.notes = trimBecause;
    // Derive target_table hint from issue type when deterministic.
    if (issue === "thumb_mismatch") detail.target_table = "image_assets";
    else if (issue === "variant_code") detail.target_table = "card_variants";
    else if (issue === "price_band") detail.target_table = "printing_market_map";
    else if (issue === "rules_text" || issue === "metadata")
      detail.target_table = "card_catalog";
    return detail;
  });
}

function buildPayload(
  item: QueueItem,
  variantId: string,
  verdict: VerdictId,
  issues: string[],
  because: string,
  source: string,
  action: ReviewBarAction,
  missingImageLink: string,
  missingImageFile: File | null,
): DevReviewHubSubmitPayload {
  const sel = item.variants.find((v) => v.id === variantId);
  const cardId = item.cardCode ?? item.id;
  const vk = sel?.variantKey ?? "";
  return {
    cardId,
    variantId,
    variantKey: vk,
    verdict,
    issues: [...issues],
    because: because.trim(),
    source: source.trim(),
    action,
    miruAssetsRelPath: sel?.miruAssetsRelPath ?? null,
    missingImageSourceUrl: missingImageLink.trim(),
    missingImageUploadName: missingImageFile?.name ?? "",
    correctionDetail: buildCorrectionDetail(
      issues,
      cardId,
      because,
      sel?.miruAssetsRelPath,
    ),
  };
}

export function ReviewDrawer({
  item,
  open,
  onOpenChange,
  onAnimationEnd,
  onSubmitted,
}: ReviewDrawerProps) {
  const variantData = React.useMemo(
    () => ({ variants: item.variants }),
    [item.variants],
  );
  const [variant, setVariant] = React.useState<string>(() =>
    pickInitialVariant(variantData.variants, item.version),
  );
  const [verdict, setVerdict] = React.useState<VerdictId | "">("");
  const [issues, setIssues] = React.useState<string[]>([]);
  const [because, setBecause] = React.useState("");
  const [source, setSource] = React.useState("");
  const [submitStatus, setSubmitStatus] = React.useState<
    "idle" | "submitting" | "success" | "error"
  >("idle");
  const [actionMessage, setActionMessage] = React.useState<string | null>(null);
  /** Staging for missing-image lane only (not the Section D citation source). */
  const [missingImageLink, setMissingImageLink] = React.useState("");
  const [missingImageFile, setMissingImageFile] = React.useState<File | null>(
    null,
  );
  const missingImageFileInputRef = React.useRef<HTMLInputElement>(null);
  const [imageLightboxOpen, setImageLightboxOpen] = React.useState(false);
  const [priceSnap, setPriceSnap] = React.useState<OperatorPriceSnapshot | null>(
    null,
  );
  const [priceLoading, setPriceLoading] = React.useState(false);
  const [refreshLoading, setRefreshLoading] = React.useState(false);
  const [refreshError, setRefreshError] = React.useState<string | null>(null);
  const [lightboxCloseGuard, setLightboxCloseGuard] = React.useState(false);
  const suppressDrawerCloseRef = React.useRef(false);
  const suppressDrawerCloseTimerRef = React.useRef<number | null>(null);
  // Mirror imageLightboxOpen into a ref so blockDismissWhileLightbox always reads the
  // synchronously-current value instead of a potentially-stale closure capture.
  const imageLightboxOpenRef = React.useRef(false);
  imageLightboxOpenRef.current = imageLightboxOpen;

  const closeLightboxOnly = React.useCallback(() => {
    suppressDrawerCloseRef.current = true;
    setLightboxCloseGuard(true);
    if (
      suppressDrawerCloseTimerRef.current != null &&
      typeof window !== "undefined"
    ) {
      window.clearTimeout(suppressDrawerCloseTimerRef.current);
    }
    setImageLightboxOpen(false);
    if (typeof window !== "undefined") {
      suppressDrawerCloseTimerRef.current = window.setTimeout(() => {
        suppressDrawerCloseRef.current = false;
        setLightboxCloseGuard(false);
        suppressDrawerCloseTimerRef.current = null;
      }, 180);
      return;
    }
    suppressDrawerCloseRef.current = false;
    setLightboxCloseGuard(false);
  }, []);

  React.useEffect(() => {
    const m = { variants: item.variants };
    setVariant(pickInitialVariant(m.variants, item.version));
    setVerdict("");
    setIssues([]);
    setBecause("");
    setSource("");
    setSubmitStatus("idle");
    setActionMessage(null);
    setMissingImageLink("");
    setMissingImageFile(null);
    setImageLightboxOpen(false);
    setLightboxCloseGuard(false);
    setPriceSnap(null);
    setRefreshError(null);
    suppressDrawerCloseRef.current = false;
    if (
      suppressDrawerCloseTimerRef.current != null &&
      typeof window !== "undefined"
    ) {
      window.clearTimeout(suppressDrawerCloseTimerRef.current);
      suppressDrawerCloseTimerRef.current = null;
    }
  }, [item]);

  React.useEffect(
    () => () => {
      if (
        suppressDrawerCloseTimerRef.current != null &&
        typeof window !== "undefined"
      ) {
        window.clearTimeout(suppressDrawerCloseTimerRef.current);
      }
    },
    [],
  );

  React.useEffect(() => {
    setMissingImageLink("");
    setMissingImageFile(null);
  }, [variant]);

  React.useEffect(() => {
    if (verdict === "looks_correct") {
      setIssues([]);
      setBecause("");
      setSource("");
    }
  }, [verdict]);

  const sectionE = React.useMemo(
    () => item.miruHistory ?? [],
    [item.miruHistory],
  );

  const resolvedVariantImageUrl = React.useMemo(
    () => getVariantImageUrl(variantData, variant),
    [variantData, variant],
  );

  const [imageLoadFailed, setImageLoadFailed] = React.useState(false);
  const [imageLoaded, setImageLoaded] = React.useState(false);
  React.useEffect(() => {
    setImageLoadFailed(false);
    setImageLoaded(false);
  }, [resolvedVariantImageUrl]);

  const selectedVariantLabel = React.useMemo(() => {
    return variantData.variants.find((v) => v.id === variant)?.label ?? variant;
  }, [variantData.variants, variant]);

  const showVariantImage =
    Boolean(resolvedVariantImageUrl) && !imageLoadFailed;

  const setCodeNumber = `${item.setCode} | #${item.cardNumber}`;

  const showCAndD = verdict !== "" && verdict !== "looks_correct";

  const meta = `${item.setCode} | #${item.cardNumber}`;

  const runAction = React.useCallback(
    async (action: ReviewBarAction) => {
      setActionMessage(null);
      let check =
        action === "approve"
          ? validateApprove(verdict, issues, because, source)
          : action === "fix_it"
            ? validateFixIt(verdict, issues, because, source)
            : validateHold(verdict);

      if (!check.ok) {
        setActionMessage(check.reason);
        setSubmitStatus("error");
        return;
      }

      if (!verdict) return;
      const payload = buildPayload(
        item,
        variant,
        verdict,
        issues,
        because,
        source,
        action,
        missingImageLink,
        missingImageFile,
      );

      setSubmitStatus("submitting");
      const res = await submitDevTrainingReview(payload);
      if (res.ok) {
        setSubmitStatus("success");
        setActionMessage(
          action === "approve"
            ? "Saved to Miru training store."
            : action === "fix_it"
              ? "Fix it saved."
              : "Hold saved.",
        );
        onSubmitted?.();
      } else {
        setSubmitStatus("error");
        setActionMessage(res.error);
      }
    },
    [
      because,
      item,
      issues,
      missingImageFile,
      missingImageLink,
      onSubmitted,
      source,
      variant,
      verdict,
    ],
  );

  const busy = submitStatus === "submitting";

  const latestHistory = sectionE[0];
  const olderHistory = sectionE.slice(1);

  React.useEffect(() => {
    if (!open || !item.cardCode) return;
    const pid = parseInt(variant, 10);
    if (Number.isNaN(pid)) return;
    const vk =
      variantData.variants.find((x) => x.id === variant)?.variantKey ?? "";
    setPriceLoading(true);
    setRefreshError(null);
    fetchOperatorPriceContext({
      printingId: pid,
      cardCode: item.cardCode,
      variantKey: vk,
    })
      .then((s) => setPriceSnap(s))
      .finally(() => setPriceLoading(false));
  }, [open, item.cardCode, item.id, variant, variantData.variants]);

  const runPriceRefresh = React.useCallback(async () => {
    if (!item.cardCode) return;
    const pid = parseInt(variant, 10);
    if (Number.isNaN(pid)) return;
    const vk =
      variantData.variants.find((x) => x.id === variant)?.variantKey ?? "";
    setRefreshLoading(true);
    setRefreshError(null);
    const res = await refreshOperatorPrice({
      printingId: pid,
      cardCode: item.cardCode,
      variantKey: vk,
    });
    setRefreshLoading(false);
    if (res.ok && res.snapshot) {
      setPriceSnap(res.snapshot);
      return;
    }
    setRefreshError(res.error ?? "Refresh failed");
  }, [item.cardCode, variant, variantData.variants]);

  const fp = freshnessPill(priceSnap?.freshness, Boolean(refreshError));

  // Radix Dialog (Vaul) treats any pointer "outside" drawer content as dismiss — including
  // the portaled image lightbox. Prevent dismiss + ignore spurious onOpenChange(false) while
  // the lightbox is open so closing the image does not close the review drawer.
  const handleDrawerOpenChange = React.useCallback(
    (next: boolean) => {
      if (
        !next &&
        (imageLightboxOpen ||
          lightboxCloseGuard ||
          suppressDrawerCloseRef.current)
      ) {
        return;
      }
      onOpenChange(next);
    },
    [imageLightboxOpen, lightboxCloseGuard, onOpenChange],
  );

  // Read exclusively from refs so this callback is always current — no stale closure.
  // suppressDrawerCloseRef is set synchronously in closeLightboxOnly before any async
  // state update, so it correctly blocks Vaul dismiss during the entire guard window.
  const blockDismissWhileLightbox = React.useCallback(
    (e: { preventDefault: () => void }) => {
      if (imageLightboxOpenRef.current || suppressDrawerCloseRef.current) {
        e.preventDefault();
      }
    },
    [],
  );

  return (
    <>
      <ImageLightboxOverlay
        open={
          imageLightboxOpen &&
          Boolean(showVariantImage && resolvedVariantImageUrl)
        }
        imageUrl={resolvedVariantImageUrl ?? ""}
        label={`${item.name} | ${selectedVariantLabel}`}
        onClose={closeLightboxOnly}
      />
      <Drawer
        open={open}
        onOpenChange={handleDrawerOpenChange}
        onAnimationEnd={onAnimationEnd}
        dismissible={!imageLightboxOpen && !lightboxCloseGuard}
        shouldScaleBackground
        setBackgroundColorOnScale={false}
      >
      <DrawerContent
        className="mx-auto flex min-h-0 w-full max-w-iphone flex-col overflow-hidden border-drh-stroke p-0"
        onPointerDownOutside={blockDismissWhileLightbox}
        onInteractOutside={blockDismissWhileLightbox}
      >
        <DrawerTitle className="sr-only">Review {item.name}</DrawerTitle>
        <DrawerDescription className="sr-only">
          Dev review bottom sheet for {meta}.
        </DrawerDescription>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div className="shrink-0 overflow-x-hidden px-2.5 pb-1 pt-[max(0.25rem,env(safe-area-inset-top,0px))]">
            <DrawerHandle className="mx-auto mb-1.5 mt-1 h-1 w-9 shrink-0 rounded-full bg-zinc-500/90" />
            <header className="flex items-start justify-between gap-2 pb-1.5">
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-drh-muted">
                  Review
                </p>
                <h2 className="truncate text-base font-semibold leading-tight text-drh-text">
                  {item.name}
                </h2>
                <p className="mt-0.5 text-xs text-drh-muted">{meta}</p>
              </div>
              <DrawerClose asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="shrink-0 text-drh-muted"
                  aria-label="Close review"
                >
                  <X className="h-4 w-4" />
                </Button>
              </DrawerClose>
            </header>

            <div className="pb-1">
              <p className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-drh-muted">
                Variant
              </p>
              <ToggleGroup
                type="single"
                value={variant}
                onValueChange={(v) => v && setVariant(v)}
                variant="outline"
                size="sm"
                className="flex-wrap justify-start gap-1"
                aria-label="Select variant"
              >
                {variantData.variants.map((v) => (
                  <ToggleGroupItem key={v.id} value={v.id}>
                    {v.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>

            {/* Compact preview: tap opens full-screen image view */}
            <div className="pb-2 pt-0.5">
              {showVariantImage ? (
                <div className="flex max-w-full items-start gap-2">
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-drh-muted">
                      Preview
                    </p>
                    <p className="text-[11px] leading-snug text-drh-muted">
                      Tap the image for a larger view.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setImageLightboxOpen(true)}
                    className="relative w-[72px] shrink-0 overflow-hidden rounded-xl border border-white/[0.12] bg-zinc-950/80 shadow-md ring-offset-2 ring-offset-drh-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/80"
                    style={{ aspectRatio: "63 / 88" }}
                    aria-label={`Open larger image: ${item.name}, ${selectedVariantLabel}`}
                  >
                    {!imageLoaded && (
                      <Skeleton className="absolute inset-0 rounded-xl" />
                    )}
                    <img
                      src={resolvedVariantImageUrl!}
                      alt=""
                      className="h-full w-full object-contain object-center"
                      loading="lazy"
                      onLoad={() => setImageLoaded(true)}
                      onError={() => setImageLoadFailed(true)}
                      style={imageLoaded ? undefined : { opacity: 0 }}
                    />
                  </button>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-amber-500/35 bg-zinc-950/45 px-2.5 py-2">
                  <p className="text-xs font-semibold leading-snug text-drh-text">
                    No verified image for this variant
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-drh-muted">
                    Add a source link or upload below.
                  </p>
                </div>
              )}
            </div>
          </div>

          <div
            className="min-h-0 min-w-0 flex-1 touch-pan-y overflow-y-auto overscroll-y-contain px-2.5 pb-2 [-webkit-overflow-scrolling:touch]"
            data-vaul-no-drag
          >
            <div className="flex max-w-full flex-col gap-2.5 overflow-x-hidden">
              <section aria-labelledby="sec-a">
                <h3
                  id="sec-a"
                  className="mb-1 text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                >
                  What you are checking
                </h3>
                <MagicCard>
                  <p className="text-xs leading-relaxed text-drh-text">
                    This is the card PM is currently showing.
                  </p>
                  <p className="mt-1.5 text-xs leading-relaxed text-drh-muted">
                    Confirm the artwork, variant, and pricing lane match.
                  </p>
                  <p className="mt-2 text-xs text-drh-text">
                    <span className="text-drh-muted">Selected variant: </span>
                    <span className="font-semibold">{selectedVariantLabel}</span>
                  </p>
                  <p className="mt-1 text-xs text-drh-text">
                    <span className="text-drh-muted">Set + number: </span>
                    <span className="font-mono font-medium">{setCodeNumber}</span>
                  </p>
                </MagicCard>
              </section>

              {!showVariantImage ? (
                <section aria-labelledby="sec-missing-img">
                  <h3
                    id="sec-missing-img"
                    className="mb-1 text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                  >
                    Reference image
                  </h3>
                  <MagicCard className="space-y-3">
                    <div className="space-y-2">
                      <Label
                        htmlFor={`missing-img-url-${item.id}`}
                        className="text-[11px] text-drh-muted"
                      >
                        Source link
                      </Label>
                      <Input
                        id={`missing-img-url-${item.id}`}
                        value={missingImageLink}
                        onChange={(e) => setMissingImageLink(e.target.value)}
                        placeholder="https://…"
                        className="text-base sm:text-sm"
                        inputMode="url"
                        autoComplete="off"
                      />
                    </div>
                    <div className="space-y-2">
                      <input
                        ref={missingImageFileInputRef}
                        type="file"
                        accept="image/*"
                        className="sr-only"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          setMissingImageFile(f ?? null);
                        }}
                      />
                      <Button
                        type="button"
                        variant="outline"
                        className="h-11 w-full text-xs"
                        onClick={() => missingImageFileInputRef.current?.click()}
                      >
                        {missingImageFile
                          ? missingImageFile.name
                          : "Upload reference image"}
                      </Button>
                    </div>
                  </MagicCard>
                </section>
              ) : null}

              <section aria-labelledby="sec-e">
                <h3
                  id="sec-e"
                  className="mb-1 text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                >
                  Prior decisions (Miru)
                </h3>
                <MagicCard>
                  <p className="mb-2 text-[11px] leading-relaxed text-drh-muted">
                    Each submission is logged. To correct a mistake, submit again
                    with the right verdict — the new row becomes the latest
                    record.
                  </p>
                  {sectionE.length === 0 ? (
                    <p className="text-xs text-drh-muted">
                      No prior training reviews for this card code yet.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-[10px] text-drh-muted">
                        Times in Los Angeles (Pacific).
                      </p>
                      {latestHistory ? (
                        <div className="rounded-lg border border-amber-500/30 bg-zinc-950/55 px-2.5 py-2">
                          <p className="text-[10px] font-medium uppercase tracking-wide text-amber-400/95">
                            Latest prior decision
                          </p>
                          <p className="mt-1 text-xs font-semibold text-drh-text">
                            {formatHistoryWhen(latestHistory)}
                          </p>
                          <p className="mt-1 font-mono text-[11px] text-drh-muted">
                            {(latestHistory.action || "—") +
                              " · " +
                              (latestHistory.verdict || "—") +
                              (latestHistory.variantKey
                                ? ` · ${latestHistory.variantKey}`
                                : "")}
                          </p>
                          <p className="mt-1.5 text-xs leading-relaxed text-drh-muted">
                            {latestHistory.body}
                          </p>
                        </div>
                      ) : null}
                      {olderHistory.length > 0 ? (
                        <details className="rounded-md border border-white/[0.06] bg-zinc-950/35 px-2 py-1.5">
                          <summary className="cursor-pointer select-none text-[11px] text-drh-muted">
                            Older entries ({olderHistory.length})
                          </summary>
                          <ul className="mt-2 space-y-2.5 border-t border-white/[0.06] pt-2">
                            {olderHistory.map((entry) => (
                              <li key={entry.id} className="text-[11px]">
                                <p className="font-medium text-drh-text">
                                  {formatHistoryWhen(entry)}
                                </p>
                                <p className="mt-0.5 font-mono text-drh-muted">
                                  {(entry.action || "—") +
                                    " · " +
                                    (entry.verdict || "—") +
                                    (entry.variantKey
                                      ? ` · ${entry.variantKey}`
                                      : "")}
                                </p>
                                <p className="mt-0.5 leading-relaxed text-drh-muted">
                                  {entry.body}
                                </p>
                              </li>
                            ))}
                          </ul>
                        </details>
                      ) : null}
                    </div>
                  )}
                </MagicCard>
              </section>

              <section aria-labelledby="sec-price">
                <h3
                  id="sec-price"
                  className="mb-1 text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                >
                  Market price
                </h3>
                <MagicCard className="min-w-0 space-y-2 break-words">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                        fp.className,
                      )}
                    >
                      {fp.label}
                    </span>
                    <Button
                      type="button"
                      variant="outline"
                      className="h-8 gap-1 px-2 text-[11px]"
                      disabled={
                        busy ||
                        priceLoading ||
                        refreshLoading ||
                        !priceSnap?.ok ||
                        priceSnap.mapping?.truth === "unresolved" ||
                        priceSnap.mapping?.truth === "unmapped"
                      }
                      onClick={() => void runPriceRefresh()}
                      aria-label="Refresh price from local TCGCSV snapshot"
                    >
                      <RefreshCw
                        className={cn(
                          "h-3.5 w-3.5",
                          refreshLoading && "animate-spin",
                        )}
                      />
                      Refresh
                    </Button>
                  </div>
                  {priceLoading ? (
                    <Skeleton className="h-10 w-full rounded-md" />
                  ) : null}
                  {!priceLoading && priceSnap && !priceSnap.ok ? (
                    <p className="text-xs text-amber-400/95">{priceSnap.error}</p>
                  ) : null}
                  {!priceLoading && priceSnap?.ok ? (
                    <>
                      {priceSnap.mapping?.truth === "unresolved" ||
                      priceSnap.mapping?.truth === "unmapped" ? (
                        <p className="text-[11px] leading-snug text-drh-muted">
                          {priceSnap.mapping?.truth === "unmapped"
                            ? "No marketplace mapping for this printing — price lane unresolved (fail-closed)."
                            : "Multiple or conflicting marketplace mappings — price not shown."}
                        </p>
                      ) : (
                        <>
                          {priceSnap.mapping?.truth === "weak" ? (
                            <p className="text-[10px] leading-snug text-amber-400/90">
                              Mapping confidence is low — confirm product identity
                              before trusting price.
                            </p>
                          ) : null}
                          {!priceSnap.price ? (
                            <>
                              {priceSnap.marketProduct?.id ? (
                                <p className="font-mono text-[10px] text-drh-muted">
                                  Product {priceSnap.marketProduct.id}
                                  {priceSnap.marketProduct.marketVariantLabel
                                    ? ` · ${priceSnap.marketProduct.marketVariantLabel}`
                                    : ""}
                                </p>
                              ) : null}
                              <p className="text-[11px] leading-snug text-drh-muted">
                                No price row in DB yet for this mapped product. Use
                                Refresh if local TCGCSV includes this product.
                              </p>
                            </>
                          ) : (
                            <>
                              <p className="text-sm font-semibold text-drh-text">
                                {formatUsd(priceSnap.price.market)}
                                {priceSnap.price.mid != null ? (
                                  <span className="ml-2 text-[11px] font-normal text-drh-muted">
                                    mid {formatUsd(priceSnap.price.mid)}
                                  </span>
                                ) : null}
                              </p>
                              <p className="font-mono text-[10px] text-drh-muted">
                                {priceSnap.marketProduct?.id
                                  ? `Product ${priceSnap.marketProduct.id}`
                                  : "—"}
                                {priceSnap.marketProduct?.marketVariantLabel
                                  ? ` · ${priceSnap.marketProduct.marketVariantLabel}`
                                  : ""}
                                {priceSnap.price.subtypeName
                                  ? ` · ${priceSnap.price.subtypeName}`
                                  : ""}
                              </p>
                              <p className="text-[10px] text-drh-muted">
                                {priceSnap.marketProduct?.source === "tcgcsv"
                                  ? "Source: TCGCSV → market_prices"
                                  : `Source: ${priceSnap.marketProduct?.source ?? "—"}`}
                              </p>
                              <p className="text-[10px] text-drh-muted">
                                {priceSnap.price.capturedAtIso
                                  ? `Updated (LA): ${formatLosAngelesDateTime(priceSnap.price.capturedAtIso)}`
                                  : "Updated: —"}
                              </p>
                            </>
                          )}
                        </>
                      )}
                    </>
                  ) : null}
                  {refreshError ? (
                    <p className="text-[11px] text-amber-400/95" role="alert">
                      {refreshError}
                    </p>
                  ) : null}
                </MagicCard>
              </section>

              <section aria-labelledby="sec-b">
                <h3
                  id="sec-b"
                  className="mb-1 text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                >
                  Verdict
                </h3>
                <ToggleGroup
                  type="single"
                  value={verdict || undefined}
                  onValueChange={(v) => v && setVerdict(v as VerdictId)}
                  variant="outline"
                  size="sm"
                  className="flex w-full flex-wrap justify-stretch gap-1"
                  aria-label="Verdict"
                >
                  {VERDICT_OPTIONS.map((o) => (
                    <ToggleGroupItem
                      key={o.value}
                      value={o.value}
                      className="min-w-0 flex-1 text-[11px] leading-tight"
                    >
                      {o.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                {!verdict ? (
                  <p className="mt-1 text-[11px] text-drh-muted">
                    Select a verdict to continue.
                  </p>
                ) : null}
              </section>

              {showCAndD ? (
                <>
                  <section aria-labelledby="sec-c">
                    <h3
                      id="sec-c"
                      className="mb-1 text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                    >
                      What&apos;s wrong?
                    </h3>
                    <ToggleGroup
                      type="multiple"
                      value={issues}
                      onValueChange={setIssues}
                      variant="outline"
                      size="sm"
                      className="flex flex-wrap justify-start gap-1.5"
                      aria-label="Issues"
                    >
                      {ISSUE_CHIP_OPTIONS.map((chip) => (
                        <ToggleGroupItem key={chip.id} value={chip.id}>
                          {chip.label}
                        </ToggleGroupItem>
                      ))}
                    </ToggleGroup>
                  </section>

                  <section aria-labelledby="sec-d" className="space-y-2">
                    <h3
                      id="sec-d"
                      className="text-[11px] font-medium uppercase tracking-wide text-drh-muted"
                    >
                      Why
                    </h3>
                    <div className="space-y-2">
                      <div>
                        <Label htmlFor={`because-${item.id}`} variant="section">
                          Because
                        </Label>
                        <Textarea
                          id={`because-${item.id}`}
                          value={because}
                          onChange={(e) => setBecause(e.target.value)}
                          placeholder="Explain what is wrong (operator note)."
                          className="mt-1 text-base sm:text-sm"
                          autoComplete="off"
                          aria-required
                        />
                      </div>
                      <div>
                        <Label htmlFor={`source-${item.id}`} variant="section">
                          Source
                        </Label>
                        <Input
                          id={`source-${item.id}`}
                          value={source}
                          onChange={(e) => setSource(e.target.value)}
                          placeholder="URL, path, or reference"
                          className="mt-1 text-base sm:text-sm"
                          autoComplete="off"
                          aria-required
                        />
                      </div>
                    </div>
                  </section>
                </>
              ) : null}
            </div>
          </div>

          <motion.footer
            initial={{ opacity: 0.94, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className={cn(
              "relative z-10 shrink-0 border-t border-white/[0.06] bg-drh-bg/98 px-2.5 pt-2 backdrop-blur-md backdrop-saturate-150",
              "flex flex-col justify-end gap-1.5 shadow-[0_-4px_24px_rgba(0,0,0,0.35)]",
              "pb-[max(14px,calc(12px+env(safe-area-inset-bottom,0px)))]",
            )}
          >
            {actionMessage ? (
              <p
                className={cn(
                  "text-center text-[11px]",
                  submitStatus === "error"
                    ? "text-amber-400/95"
                    : "text-drh-muted",
                )}
                role="status"
                aria-live="polite"
              >
                {actionMessage}
              </p>
            ) : null}
            <p className="sr-only" id="action-rail-label">
              Review actions
            </p>
            <div
              className="grid grid-cols-3 gap-2"
              role="group"
              aria-labelledby="action-rail-label"
            >
              <Button
                type="button"
                variant="outline"
                className="h-11 text-xs"
                disabled={busy}
                onClick={() => runAction("fix_it")}
              >
                Fix it
              </Button>
              <Button
                type="button"
                variant="default"
                className="h-11 text-xs"
                disabled={busy}
                onClick={() => runAction("hold")}
              >
                Hold
              </Button>
              <ShimmerButton
                type="button"
                className="h-11 text-xs font-semibold"
                disabled={busy}
                onClick={() => runAction("approve")}
              >
                Approve
              </ShimmerButton>
            </div>
          </motion.footer>
        </div>
      </DrawerContent>
    </Drawer>
    </>
  );
}
