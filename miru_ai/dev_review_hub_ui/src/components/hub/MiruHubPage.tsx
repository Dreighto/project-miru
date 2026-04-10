import { useEffect, useState } from "react";
import type { HubSummary } from "./types";
import { HubHeader } from "./HubHeader";
import { WhatsGoingOn } from "./WhatsGoingOn";
import { VerifiedCounts } from "./VerifiedCounts";
import { PipelineStatus } from "./PipelineStatus";
import { BasicStats } from "./BasicStats";
import { RunControls } from "./RunControls";
import { QuickLinks } from "./QuickLinks";
import { MiruNavBar } from "./MiruNavBar";

const REFRESH_INTERVAL = 30_000;

export function MiruHubPage() {
  const [data, setData] = useState<HubSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const res = await fetch("/api/hub/summary");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (active) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Fetch failed");
      }
    }

    load();
    const timer = setInterval(load, REFRESH_INTERVAL);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  if (error && !data) {
    return (
      <div className="flex min-h-dvh flex-col overflow-x-hidden">
        <MiruNavBar />
        <div className="flex flex-1 flex-col items-center justify-center px-4 pb-[max(2rem,env(safe-area-inset-bottom,0px))] pt-2 text-center">
          <img
            src="/static/icons/miru-fruit.png"
            alt="Miru"
            className="mb-4 h-12 w-12 opacity-40"
          />
          <p className="text-sm text-drh-muted">
            Unable to load hub data: {error}
          </p>
          <button
            className="mt-3 text-xs text-[#c9a84c] underline"
            onClick={() => location.reload()}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex min-h-dvh flex-col overflow-x-hidden">
        <MiruNavBar />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 pb-[max(2rem,env(safe-area-inset-bottom,0px))] pt-2">
          <img
            src="/static/icons/miru-fruit.png"
            alt="Miru"
            className="h-12 w-12 animate-pulse"
          />
          <p className="text-xs text-drh-muted">Loading Miru Hub...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col overflow-x-hidden">
      <MiruNavBar />
      <div className="mx-auto w-full max-w-[500px] flex-1 space-y-6 pb-[max(3rem,calc(env(safe-area-inset-bottom,0px)+2.25rem))] pt-2">
        <HubHeader data={data} />
        <WhatsGoingOn data={data} />
        <VerifiedCounts data={data} />
        <PipelineStatus data={data} />
        <BasicStats data={data} />
        <RunControls data={data} />
        <QuickLinks />
        <footer className="pb-[max(0.5rem,env(safe-area-inset-bottom,0px))] pt-4 text-center text-[10px] text-drh-muted/40">
          Last updated {data.updated_at}
        </footer>
      </div>
    </div>
  );
}
