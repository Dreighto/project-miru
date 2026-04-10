import type { LucideIcon } from "lucide-react";
import { Terminal, SlidersHorizontal, HeartPulse } from "lucide-react";
import { MagicCard } from "@/components/magicui/magic-card";

interface QuickLink {
  href: string;
  label: string;
  Icon: LucideIcon;
  external?: boolean;
}

const links: QuickLink[] = [
  { href: "/dev", label: "Dev", Icon: Terminal },
  { href: "/dev/operator-console", label: "Operator Console", Icon: SlidersHorizontal },
  { href: "/api/health", label: "Health JSON", Icon: HeartPulse, external: true },
];

export function QuickLinks() {
  return (
    <section className="px-4">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-drh-muted mb-3">
        Quick Links
      </h2>
      <div className="grid grid-cols-2 gap-2.5">
        {links.map(({ href, label, Icon, external }) => (
          <a
            key={href}
            href={href}
            target={external ? "_blank" : undefined}
            rel={external ? "noopener noreferrer" : undefined}
            className="no-underline"
          >
            <MagicCard className="transition-opacity hover:opacity-80">
              <div className="flex items-center gap-2.5">
                <span className="w-7 h-7 rounded-md bg-white/[0.05] flex items-center justify-center text-[#c9a84c] shrink-0">
                  <Icon size={15} strokeWidth={1.75} />
                </span>
                <span className="text-sm text-drh-text font-medium leading-snug">
                  {label}
                </span>
              </div>
            </MagicCard>
          </a>
        ))}
      </div>
    </section>
  );
}
