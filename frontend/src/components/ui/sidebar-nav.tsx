"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { LucideIcon } from "lucide-react";

import { isComingSoon } from "@/lib/coming-soon";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

export interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
  /** 已开通路由的路径；无 href = 占位项（不导航、不高亮）。 */
  href?: string;
  /** 仅管理员可见（如数据看板）。由容器层按角色过滤后再传入，SidebarNav 本身不判角色。 */
  adminOnly?: boolean;
}

const baseClass =
  "flex items-center gap-3.5 rounded-field px-4 py-3 text-sm outline-none transition-colors focus-visible:shadow-focus-gold";
const activeClass = "bg-grad-gold text-ink shadow-nav-active";
const idleClass = "text-ink-soft hover:bg-glass-soft";

/** 当前路径是否命中该导航项：根路径精确匹配，其余匹配自身或子路径（如 /batch 命中 /batch/x）。 */
function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

/**
 * 侧边栏导航（BATCH-PROD-UI-0001-FIX3）。active 态由 usePathname 推导（直达 /batch 亦正确高亮），不再本地
 * state。已开通项渲染 Link 真导航；占位项渲染无导航按钮（保留外观 + 悬停「即将上线」，点击不改路由、不高亮）。
 */
export function SidebarNav({ items }: { items: NavItem[] }) {
  const pathname = usePathname() ?? "";
  return (
    <nav className="flex flex-col gap-1">
      {items.map((item) => {
        const Icon = item.icon;
        // 「即将上线」板块：名称追加后缀（点进去是统一占位页）。
        const soon = isComingSoon(item.key);
        const label = soon ? `${item.label}${copy.comingSoon.navSuffix}` : item.label;
        if (item.href) {
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.key}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(baseClass, active ? activeClass : idleClass)}
            >
              <Icon size={19} className="w-5 flex-none" strokeWidth={1.8} />
              <span>{label}</span>
            </Link>
          );
        }
        // 占位项：保留外观，点击不导航、不高亮；悬停提示「即将上线」。
        return (
          <button
            key={item.key}
            type="button"
            title={copy.nav.comingSoon}
            aria-disabled
            className={cn(baseClass, idleClass, "cursor-default")}
          >
            <Icon size={19} className="w-5 flex-none" strokeWidth={1.8} />
            <span>{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
