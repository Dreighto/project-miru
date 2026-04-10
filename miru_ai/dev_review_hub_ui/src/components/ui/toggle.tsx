import * as React from "react";
import * as TogglePrimitive from "@radix-ui/react-toggle";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const toggleVariants = cva(
  "inline-flex items-center justify-center rounded-md text-xs font-medium transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500/40 disabled:pointer-events-none disabled:opacity-50 data-[state=on]:bg-zinc-700 data-[state=on]:text-drh-text",
  {
    variants: {
      variant: {
        default: "bg-transparent text-drh-muted",
        outline:
          "border border-drh-stroke bg-transparent text-drh-text hover:bg-white/[0.04]",
      },
      size: {
        default: "h-9 min-w-[2.25rem] px-2",
        sm: "h-8 min-w-8 px-2 text-[11px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

const Toggle = React.forwardRef<
  React.ElementRef<typeof TogglePrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof TogglePrimitive.Root> &
    VariantProps<typeof toggleVariants>
>(({ className, variant, size, ...props }, ref) => (
  <TogglePrimitive.Root
    ref={ref}
    className={cn(toggleVariants({ variant, size, className }))}
    {...props}
  />
));

Toggle.displayName = TogglePrimitive.Root.displayName;

export { Toggle, toggleVariants };
