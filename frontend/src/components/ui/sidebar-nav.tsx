"use client";

import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
}

/** Vertical nav. Active item = gold-gradient fill + white text + lift shadow;
 *  others are transparent with a soft white hover. */
export function SidebarNav({
  items,
  activeKey,
  onSelect
}: {
  items: NavItem[];
  activeKey: string;
  onSelect?: (key: string) => void;
}) {
  return (
    <nav className="flex flex-col gap-1">
      {items.map((item) => {
        const active = item.key === activeKey;
        const Icon = item.icon;
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onSelect?.(item.key)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3.5 rounded-field px-4 py-3 text-sm outline-none transition-colors focus-visible:shadow-focus-gold",
              active
                ? "bg-grad-gold text-ink shadow-nav-active"
                : "text-ink-soft hover:bg-glass-soft"
            )}
          >
            <Icon size={19} className="w-5 flex-none" strokeWidth={1.8} />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
