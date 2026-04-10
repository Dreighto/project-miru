import * as React from "react";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Input } from "@/components/ui/input";

interface EvidencePanelProps {
  onImageSelected: (file: File) => void;
  onLinkProvided: (url: string) => void;
}

export function EvidencePanel({ onImageSelected, onLinkProvided }: EvidencePanelProps) {
  const [linkValue, setLinkValue] = React.useState("");
  const [activeTab, setActiveTab] = React.useState<"image" | "link">("image");
  const fileRef = React.useRef<HTMLInputElement>(null);

  const handleFileChange = React.useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (f) onImageSelected(f);
    },
    [onImageSelected],
  );

  const handleLinkSubmit = React.useCallback(() => {
    const trimmed = linkValue.trim();
    if (trimmed && /^https?:\/\/.+/.test(trimmed)) {
      onLinkProvided(trimmed);
      setLinkValue("");
    }
  }, [linkValue, onLinkProvided]);

  return (
    <Accordion type="single" collapsible className="w-full">
      <AccordionItem value="evidence" className="border-drh-stroke/50">
        <AccordionTrigger
          className="py-2 text-[11px] font-semibold uppercase tracking-wide text-drh-muted hover:text-drh-text hover:no-underline"
          data-evidence-toggle
        >
          Add Evidence
        </AccordionTrigger>
        <AccordionContent className="pb-2">
          <div className="flex gap-2 pb-2">
            <button
              type="button"
              onClick={() => setActiveTab("image")}
              className={`rounded-md px-3 py-1 text-[11px] font-semibold ${
                activeTab === "image"
                  ? "bg-amber-500/20 text-amber-400"
                  : "bg-drh-surface text-drh-muted"
              }`}
            >
              Image
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("link")}
              className={`rounded-md px-3 py-1 text-[11px] font-semibold ${
                activeTab === "link"
                  ? "bg-amber-500/20 text-amber-400"
                  : "bg-drh-surface text-drh-muted"
              }`}
            >
              Link
            </button>
          </div>
          {activeTab === "image" ? (
            <div>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFileChange}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="w-full rounded-md border border-dashed border-drh-stroke bg-drh-surface/50 px-3 py-4 text-[12px] text-drh-muted transition-colors hover:border-amber-500/40 hover:text-amber-400"
              >
                Tap to upload image
              </button>
            </div>
          ) : (
            <div className="flex gap-2">
              <Input
                type="url"
                placeholder="https://..."
                value={linkValue}
                onChange={(e) => setLinkValue(e.target.value)}
                className="flex-1 border-drh-stroke bg-drh-surface text-[14px] text-drh-text placeholder:text-drh-muted/50"
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleLinkSubmit();
                }}
              />
              <button
                type="button"
                onClick={handleLinkSubmit}
                className="rounded-md bg-amber-500/20 px-3 text-[12px] font-semibold text-amber-400 transition-colors hover:bg-amber-500/30"
              >
                Go
              </button>
            </div>
          )}
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
