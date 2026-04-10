/**
 * Magic UI–style animated list: staggered row entrance (Framer Motion).
 * Kept self-contained for Miru Dev Review Hub; pattern inspired by magicui.design animated-list.
 */
import * as React from "react";
import { isValidElement } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export interface AnimatedListProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function AnimatedList({ className, children, ...rest }: AnimatedListProps) {
  const items = React.Children.toArray(children);
  return (
    <div className={cn("flex flex-col gap-1.5", className)} role="list" {...rest}>
      {items.map((child, i) => (
        <motion.div
          key={
            isValidElement(child) && child.key != null
              ? String(child.key)
              : `row-${i}`
          }
          role="listitem"
          className="w-full min-w-0"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.24,
            delay: Math.min(i * 0.03, 0.4),
            ease: [0.22, 1, 0.36, 1],
          }}
        >
          {child}
        </motion.div>
      ))}
    </div>
  );
}
