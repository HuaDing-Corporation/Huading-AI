"use client";

import { useState } from "react";

import { Glass } from "@/components/ui/glass";
import { Progress } from "@/components/ui/progress";
import { SidebarNav } from "@/components/ui/sidebar-nav";
import { useQuota } from "@/lib/api/hooks";
import { navItems } from "@/lib/mock";

export function Sidebar() {
  const [active, setActive] = useState("workbench");
  // Single quota source (same useQuota as the top-bar QuotaBadge); hide the
  // panel until real data loads so we never show a fabricated number.
  const { data: quota } = useQuota();

  return (
    <Glass className="flex flex-col gap-1 rounded-card p-4">
      <SidebarNav items={navItems} activeKey={active} onSelect={setActive} />

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
