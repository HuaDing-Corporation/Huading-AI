"use client";

import { useState } from "react";

import { AdminPager, AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { AdminAuditRow, AuditAction } from "@/lib/api/admin-console";
import { useAdminAudit, useAdminTenants } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";

const PAGE_SIZE = 20;
const ACTION_LABEL: Record<AuditAction, string> = {
  credits_adjust: copy.admin.actionCreditsAdjust,
  plan_change: copy.admin.actionPlanChange,
  status_change: copy.admin.actionStatusChange,
  voice_slot_assign: copy.admin.actionVoiceSlotAssign,
  task_retry: copy.admin.actionTaskRetry
};

/** before/after JSON 快照 → 逐键「前 → 后」行（无 before 键则只显 after，如首次分配槽位）。 */
function BeforeAfter({ row }: { row: AdminAuditRow }) {
  const before = row.before ?? {};
  const after = row.after ?? {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  if (keys.length === 0) return <span className="text-ink-faint">—</span>;
  return (
    <span className="flex flex-col gap-0.5 tabular-nums">
      {keys.map((k) => (
        <span key={k}>
          <span className="text-ink-faint">{k}：</span>
          {k in before && <span className="text-ink-soft">{String(before[k])}</span>}
          {k in before && k in after && <span className="text-ink-faint">{copy.admin.beforeAfterArrow}</span>}
          {k in after && <span className="font-medium text-ink">{String(after[k])}</span>}
        </span>
      ))}
    </span>
  );
}

// 审计日志（ADMIN-CONSOLE-UI-0001 §二.5）：只读（无编辑/删除），动作/租户筛选 + 分页，「变更前 → 变更后」逐键展示。
export default function AdminAuditPage() {
  const [action, setAction] = useState("all");
  const [tenantId, setTenantId] = useState("all");
  const [page, setPage] = useState(1);

  const tenants = useAdminTenants({ sort: "created_at", order: "desc", page: 1, page_size: 100 });
  const query = useAdminAudit({
    action: action === "all" ? "" : (action as AuditAction),
    target_tenant_id: tenantId === "all" ? undefined : tenantId,
    page,
    page_size: PAGE_SIZE
  });

  const columns: AdminColumn<AdminAuditRow>[] = [
    { key: "time", label: copy.admin.colTime, render: (r) => <span className="tabular-nums text-ink-faint">{r.created_at.replace("T", " ").slice(0, 16)}</span> },
    { key: "actor", label: copy.admin.colActor, render: (r) => <span className="text-ink-soft">{r.actor_email ?? "—"}</span> },
    { key: "action", label: copy.admin.colAction, render: (r) => <span className="text-ink">{ACTION_LABEL[r.action as AuditAction] ?? r.action}</span> },
    { key: "target", label: copy.admin.colTargetTenant, render: (r) => <span className="text-ink">{r.target_tenant_slug ?? "—"}</span> },
    { key: "diff", label: copy.admin.colBeforeAfter, render: (r) => <BeforeAfter row={r} /> },
    { key: "reason", label: copy.admin.colReason, render: (r) => <span className="text-ink-soft">{r.reason ?? "—"}</span> }
  ];

  return (
    <>
      <header className="px-1">
        <h1 className="text-[22px] font-semibold tracking-[0.5px] text-ink">{copy.admin.auditTitle}</h1>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={action} onValueChange={(v) => { setAction(v); setPage(1); }}>
          <SelectTrigger className="w-[170px]" aria-label={copy.admin.colAction}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.auditActionAll}</SelectItem>
            <SelectItem value="credits_adjust">{copy.admin.actionCreditsAdjust}</SelectItem>
            <SelectItem value="plan_change">{copy.admin.actionPlanChange}</SelectItem>
            <SelectItem value="status_change">{copy.admin.actionStatusChange}</SelectItem>
            <SelectItem value="voice_slot_assign">{copy.admin.actionVoiceSlotAssign}</SelectItem>
            <SelectItem value="task_retry">{copy.admin.actionTaskRetry}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={tenantId} onValueChange={(v) => { setTenantId(v); setPage(1); }}>
          <SelectTrigger className="w-[180px]" aria-label={copy.admin.colTargetTenant}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.usageTenantAll}</SelectItem>
            {(tenants.data?.items ?? []).map((t) => (
              <SelectItem key={t.tenant_id} value={t.tenant_id}>{t.name}（{t.slug}）</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <AdminTable
        columns={columns}
        rows={query.data?.items ?? []}
        rowKey={(r) => r.id}
        loading={query.isLoading}
        error={query.isError}
        onRetry={() => void query.refetch()}
        minWidth={860}
      />
      <AdminPager page={page} pageSize={PAGE_SIZE} total={query.data?.total ?? 0} onPage={setPage} />
    </>
  );
}
