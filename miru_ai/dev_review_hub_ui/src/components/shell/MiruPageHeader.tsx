import * as React from "react";

/** Shared Miru page header rhythm: eyebrow → title → supporting line (Hub / Operator / Dev templates). */
export const miruEyebrowClass =
  "text-[11px] font-semibold uppercase tracking-[0.14em] text-drh-muted";
export const miruTitleClass =
  "text-[1.35rem] font-bold leading-tight tracking-tight text-[#c9a84c] sm:text-2xl";
export const miruSupportingClass = "text-sm text-drh-muted leading-snug";
export const miruScopeBadgeClass =
  "inline-flex items-center rounded-md bg-amber-500/20 px-2.5 py-1 text-[11px] font-bold tracking-wide text-amber-400";

type CenterProps = {
  eyebrow: string;
  title: string;
  description: string;
  /** Optional row after supporting line (e.g. status badge) */
  footer?: React.ReactNode;
};

export function MiruPageHeaderCenter({
  eyebrow,
  title,
  description,
  footer,
}: CenterProps) {
  return (
    <div className="flex flex-col items-center gap-2 text-center">
      <p className={miruEyebrowClass}>{eyebrow}</p>
      <h1 className={miruTitleClass}>{title}</h1>
      <p className={`${miruSupportingClass} max-w-sm`}>{description}</p>
      {footer}
    </div>
  );
}
