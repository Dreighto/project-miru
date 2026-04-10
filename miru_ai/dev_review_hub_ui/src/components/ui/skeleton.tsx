import { cn } from "@/lib/utils";

/**
 * Skeleton shimmer — pure Tailwind, no Radix dep.
 * Drop in where content is resolving.
 */
export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-white/[0.06]", className)}
      {...props}
    />
  );
}
