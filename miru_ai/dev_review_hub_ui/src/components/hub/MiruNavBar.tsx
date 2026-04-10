import type { LucideIcon } from "lucide-react";
import { LayoutDashboard, Terminal, SlidersHorizontal } from "lucide-react";

interface NavLink {
  href: string;
  label: string;
  Icon: LucideIcon;
}

const NAV_LINKS: NavLink[] = [
  { href: "/", label: "Hub", Icon: LayoutDashboard },
  { href: "/dev", label: "Dev", Icon: Terminal },
  { href: "/dev/operator-console", label: "Operator", Icon: SlidersHorizontal },
];

/** Detects which nav link is active based on current pathname. */
function isActive(href: string): boolean {
  const path = window.location.pathname;
  if (href === "/") return path === "/";
  return path.startsWith(href);
}

export function MiruNavBar() {
  return (
    <nav className="sticky top-0 z-30 flex items-center justify-between border-b border-drh-stroke/60 bg-drh-bg/85 px-4 pb-2 pt-[max(0.5rem,env(safe-area-inset-top,0px))] backdrop-blur-xl">
      <span className="text-[11px] font-semibold tracking-widest text-drh-muted/60 uppercase select-none">
        Miru
      </span>
      <div className="flex items-center gap-1">
        {NAV_LINKS.map(({ href, label, Icon }) => {
          const active = isActive(href);
          return (
            <a
              key={href}
              href={href}
              className={[
                "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium no-underline transition-colors",
                active
                  ? "bg-white/[0.07] text-[#c9a84c]"
                  : "text-drh-muted hover:bg-white/[0.04] hover:text-drh-text",
              ].join(" ")}
              aria-current={active ? "page" : undefined}
            >
              <Icon size={13} strokeWidth={1.75} />
              {label}
            </a>
          );
        })}
      </div>
    </nav>
  );
}
