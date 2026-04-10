import * as React from "react";
import { cn } from "@/lib/utils";
import { Button, type ButtonProps } from "@/components/ui/button";

/**
 * Magic UI–style shimmer CTA: restrained highlight sweep on Approve.
 */
export const ShimmerButton = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, children, disabled, ...props }, ref) => {
    return (
      <Button
        ref={ref}
        disabled={disabled}
        className={cn(
          "relative overflow-hidden border border-emerald-500/30 bg-emerald-950/55 text-emerald-50 hover:bg-emerald-900/55",
          className,
        )}
        {...props}
      >
        <span className="relative z-[1]">{children}</span>
        {!disabled ? (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-0 overflow-hidden opacity-40"
          >
            <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/12 to-transparent" />
          </span>
        ) : null}
      </Button>
    );
  },
);
ShimmerButton.displayName = "ShimmerButton";
