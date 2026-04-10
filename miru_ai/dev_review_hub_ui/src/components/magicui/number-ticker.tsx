import { useEffect, useRef, useState } from "react";
import { animate } from "framer-motion";
import { cn } from "@/lib/utils";

interface NumberTickerProps {
  value: number;
  /** Animation duration in seconds. Default 1.1. */
  duration?: number;
  className?: string;
  /** Custom formatter. Defaults to toLocaleString. */
  format?: (n: number) => string;
}

/**
 * Magic UI–style number ticker.
 * Counts from previous value to current using framer-motion animate().
 * On first mount counts from 0.
 */
export function NumberTicker({
  value,
  duration = 1.1,
  className,
  format,
}: NumberTickerProps) {
  const [display, setDisplay] = useState(0);
  const prevRef = useRef(0);
  const hasMounted = useRef(false);

  useEffect(() => {
    // On first mount, count from 0; on subsequent updates, from last value.
    const from = hasMounted.current ? prevRef.current : 0;
    hasMounted.current = true;

    const controls = animate(from, value, {
      duration,
      ease: [0.22, 1, 0.36, 1],
      onUpdate(latest) {
        setDisplay(Math.round(latest));
      },
      onComplete() {
        prevRef.current = value;
      },
    });
    return () => controls.stop();
  }, [value, duration]);

  const formatted = format ? format(display) : display.toLocaleString();
  return (
    <span className={cn("tabular-nums", className)}>{formatted}</span>
  );
}
