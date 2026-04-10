import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { HubSummary } from "./types";

type RestartState = "idle" | "restarting" | "ok" | "error";

function RestartButton({
  label,
  endpoint,
  allowed,
}: {
  label: string;
  endpoint: string;
  allowed: boolean;
}) {
  const [state, setState] = useState<RestartState>("idle");

  async function handleRestart() {
    if (!allowed || state === "restarting") return;
    setState("restarting");
    try {
      const res = await fetch(endpoint, { method: "POST" });
      setState(res.ok ? "ok" : "error");
    } catch {
      setState("error");
    }
    setTimeout(() => setState("idle"), 4000);
  }

  const stateLabel =
    state === "restarting"
      ? "Restarting..."
      : state === "ok"
        ? "Sent"
        : state === "error"
          ? "Failed"
          : label;

  return (
    <Button
      variant="outline"
      className="h-10 text-sm flex-1 min-w-[140px]"
      disabled={!allowed || state === "restarting"}
      onClick={handleRestart}
    >
      {stateLabel}
    </Button>
  );
}

export function RunControls({ data }: { data: HubSummary }) {
  return (
    <section className="px-4 space-y-3">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-drh-muted">
        Controls
      </h2>
      <div className="flex flex-wrap gap-2.5">
        <RestartButton
          label="Restart Miru (18765)"
          endpoint="/api/runtime/restart/18765"
          allowed={data.restart_allowed}
        />
        <RestartButton
          label="Restart PM (18080)"
          endpoint="/api/runtime/restart/18080"
          allowed={data.restart_allowed}
        />
      </div>
      {!data.restart_allowed && (
        <p className="text-[11px] text-drh-muted/60">
          Restart controls require local network or runtime token.
        </p>
      )}
    </section>
  );
}
