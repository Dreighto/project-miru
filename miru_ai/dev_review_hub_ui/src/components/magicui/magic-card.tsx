import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Magic UI–style premium card: subtle edge definition, no loud motion (operator tool).
 */
export interface MagicCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function MagicCard({ className, children, ...rest }: MagicCardProps) {
  return (
    <div
      className={cn("relative rounded-lg p-px", className)}
      {...rest}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-lg bg-gradient-to-br from-zinc-500/25 via-zinc-600/10 to-transparent opacity-90"
      />
      <div className="relative rounded-md bg-drh-surface/95 px-2.5 py-2.5 ring-1 ring-white/[0.06]">
        {children}
      </div>
    </div>
  );
}
