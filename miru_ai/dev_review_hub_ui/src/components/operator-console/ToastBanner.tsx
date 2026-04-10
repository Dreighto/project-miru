import * as React from "react";
import { cn } from "@/lib/utils";

interface ToastBannerProps {
  message: string | null;
  variant?: "success" | "error";
  onDismiss: () => void;
}

export function ToastBanner({ message, variant = "success", onDismiss }: ToastBannerProps) {
  React.useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDismiss, 2800);
    return () => clearTimeout(t);
  }, [message, onDismiss]);

  if (!message) return null;

  return (
    <div
      className={cn(
        "fixed left-1/2 z-[120] -translate-x-1/2 rounded-lg px-4 py-2.5 text-[13px] font-medium shadow-lg transition-opacity",
        "bottom-[calc(16px+env(safe-area-inset-bottom,0px))]",
        variant === "success"
          ? "bg-[#1a3a1a] text-[#a5d6a7] ring-1 ring-[#2d6b2d]"
          : "bg-[#3a1a1a] text-[#fda4af] ring-1 ring-[#6b2d2d]",
      )}
      role="status"
    >
      {message}
    </div>
  );
}
