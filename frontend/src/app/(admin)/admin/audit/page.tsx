"use client";

import { useState } from "react";

import { AdminPager, AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { AdminAuditRow, AuditAction } from "@/lib/api/admin-console";
import { useAdminAudit, useAdminTenants } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";
import { flattenAuditDiff } from "./format";

const PAGE_SIZE = 20;
const ACTION_LABEL: Record<AuditAction, string> = {
  credits_adjust: copy.admin.actionCreditsAdjust,
  plan_change: copy.admin.actionPlanChange,
  status_change: copy.admin.actionStatusChange,
  voice_slot_assign: copy.admin.actionVoiceSlotAssign,
  task_retry: copy.admin.actionTaskRetry
};

/** 破折号（—）读屏时读「无」而非 em-dash 噪音；其它值 sr-only 前缀「变更前/后」读通语义（不只靠颜色/箭头）。 */
function DiffValue({ label, text, className }: { label: string; text: string; className: string }) {
  return (
    <span className={className}>
      <span className="sr-only">{label} </span>
      {text === copy.admin.auditEmpty ? (
        <>
          <span aria-hidden="true">{copy.admin.auditEmpty}</span>
          <span className="sr-only">{copy.admin.auditSrNone}</span>
        </>
      ) : (
        text
      )}
    </span>
  );
}

/**
 * before/after JSON 快照 → 逐键「前 → 后」行。嵌套对象经 flattenAuditDiff 展平为点号路径叶子
 * （`subscription.total`）、标量经 formatAuditValue 中文化——不再吐 [object Object]/空白/字面 null/裸布尔。
 * 单侧键只显那一侧、不画箭头（如首次分配槽位的 after 独有键）。
 */
function BeforeAfter({ row }: { row: AdminAuditRow }) {
  const leaves = flattenAuditDiff(row.before, row.after);
  if (leaves.length === 0)
    return (
      <span className="text-ink-faint">
        <span aria-hidden="true">{copy.admin.auditEmpty}</span>
        <span className="sr-only">{copy.admin.auditSrNone}</span>
      </span>
    );
  return (
    <span className="flex flex-col gap-0.5 tabular-nums">
      {leaves.map(({ key, before, after }) => (
        <span key={key}>
          <span className="text-ink-faint">{key}：</span>
          {before !== undefined && <DiffValue label={copy.admin.auditSrBefore} text={before} className="text-ink-soft" />}
          {before !== undefined && after !== undefined && (
            <span aria-hidden="true" className="text-ink-faint">
              {copy.admin.beforeAfterArrow}
            </span>
          )}
          {after !== undefined && <DiffValue label={copy.admin.auditSrAfter} text={after} className="font-medium text-ink" />}
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
