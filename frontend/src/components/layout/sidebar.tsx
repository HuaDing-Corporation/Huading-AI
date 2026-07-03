"use client";

import { Glass } from "@/components/ui/glass";
import { Progress } from "@/components/ui/progress";
import { SidebarNav } from "@/components/ui/sidebar-nav";
import { useQuota } from "@/lib/api/hooks";
import { useAuth } from "@/lib/auth/auth-context";
import { navItems } from "@/lib/nav";

export function Sidebar() {
  // active 态由路由推导（见 SidebarNav 内 usePathname），不再本地持有高亮 state。
  // Single quota source (same useQuota as the top-bar QuotaBadge); hide the
  // panel until real data loads so we never show a fabricated number.
  const { data: quota } = useQuota();
  // 仅管理员可见项（数据看板）：非管理员从导航过滤掉；页面本身仍优雅处理后端 403（不靠此兜底）。
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";
  const items = isAdmin ? navItems : navItems.filter((item) => !item.adminOnly);

  return (
    <Glass className="flex flex-col gap-1 rounded-card p-4">
      <SidebarNav items={items} />

      {quota && (
        <div className="mt-auto rounded-mark border border-line-gold bg-glass-fill px-4 py-3.5">
          <p className="text-xs text-ink-soft">
            <b className="text-ink">本月额度</b>
          </p>
          <p className="mt-0.5 text-[11.5px] text-ink-soft">
            已用 {quota.used} / {quota.total} 条
          </p>
          <Progress value={quota.total ? Math.round((quota.used / quota.total) * 100) : 0} className="mt-2.5" />
        </div>
      )}
    </Glass>
  );
}
