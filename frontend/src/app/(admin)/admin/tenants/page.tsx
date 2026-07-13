"use client";

import { useState } from "react";

import { AdminPager, AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { TenantDetailDialog } from "@/components/admin/tenant-detail";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { AdminTenantRow, AdminTenantSort, PlanCode, TenantStatus } from "@/lib/api/admin-console";
import { useAdminTenants } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";

const LIMIT = 20;
const fmt = (n: number) => n.toLocaleString("zh-CN");

// 租户/用户管理（ADMIN-CONSOLE-UI-0001 §二.1）：搜索 + 套餐/状态筛选 + 分页列表 → 详情弹窗（含三个写操作）。
export default function AdminTenantsPage() {
  const [search, setSearch] = useState("");
  const [plan, setPlan] = useState("all");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState<AdminTenantSort>("created_desc");
  const [offset, setOffset] = useState(0);
  const [detailId, setDetailId] = useState<string | null>(null);

  const query = useAdminTenants({
    search: search || undefined,
    plan: plan === "all" ? "" : (plan as PlanCode),
    status: status === "all" ? "" : (status as TenantStatus),
    sort,
    limit: LIMIT,
    offset
  });

  const columns: AdminColumn<AdminTenantRow>[] = [
    {
      key: "tenant",
      label: copy.admin.colTenant,
      render: (t) => (
        <span className="text-ink">
          {t.name}
          <span className="ml-1 text-ink-faint">({t.slug})</span>
        </span>
      )
    },
    { key: "owner", label: copy.admin.colOwner, render: (t) => <span className="text-ink-soft">{t.owner_email}</span> },
    { key: "plan", label: copy.admin.colPlan, render: (t) => <span className="text-ink">{t.plan_code}</span> },
    {
      key: "balance",
      label: copy.admin.colBalance,
      align: "right",
      render: (t) => (
        <span className="tabular-nums text-ink-soft">
          {fmt(t.balance.total)} / {fmt(t.balance.used)} / {fmt(t.balance.reserved)} /{" "}
          <b className="text-success-fg">{fmt(t.balance.remaining)}</b>
        </span>
      )
    },
    {
      key: "status",
      label: copy.admin.colStatus,
      render: (t) =>
        t.status === "active" ? (
          <span className="text-success-fg">{copy.admin.statusActive}</span>
        ) : (
          // 危险态不只靠颜色：文字本身即「停用」
          <span className="font-medium text-error-fg">{copy.admin.statusDisabled}</span>
        )
    },
    { key: "created", label: copy.admin.colCreated, render: (t) => <span className="text-ink-faint">{t.created_at.slice(0, 10)}</span> },
    { key: "tasks", label: copy.admin.colTasks, align: "right", render: (t) => fmt(t.task_count) },
    {
      key: "actions",
      label: copy.admin.colActions,
      render: (t) => (
        <button
          type="button"
          onClick={() => setDetailId(t.tenant_id)}
          className="rounded-field border border-line-gold bg-glass-fill px-2.5 py-1 text-[12px] text-gold-deep hover:bg-glass-hover"
        >
          {copy.admin.viewDetail}
        </button>
      )
    }
  ];

  return (
    <>
      <header className="px-1">
        <h1 className="text-[22px] font-semibold tracking-[0.5px] text-ink">{copy.admin.tenantsTitle}</h1>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label={copy.admin.tenantSearchPlaceholder}
          placeholder={copy.admin.tenantSearchPlaceholder}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
          className="w-[280px]"
        />
        <Select
          value={plan}
          onValueChange={(v) => {
            setPlan(v);
            setOffset(0);
          }}
        >
          <SelectTrigger className="w-[140px]" aria-label={copy.admin.colPlan}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.planFilterAll}</SelectItem>
            <SelectItem value="free">{copy.admin.planFree}</SelectItem>
            <SelectItem value="basic">{copy.admin.planBasic}</SelectItem>
            <SelectItem value="huading">{copy.admin.planHuading}</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={status}
          onValueChange={(v) => {
            setStatus(v);
            setOffset(0);
          }}
        >
          <SelectTrigger className="w-[130px]" aria-label={copy.admin.colStatus}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.statusFilterAll}</SelectItem>
            <SelectItem value="active">{copy.admin.statusActive}</SelectItem>
            <SelectItem value="disabled">{copy.admin.statusDisabled}</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={sort}
          onValueChange={(v) => {
            setSort(v as AdminTenantSort);
            setOffset(0);
          }}
        >
          <SelectTrigger className="w-[170px]" aria-label={copy.admin.sortLabel}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="created_desc">{copy.admin.sortCreatedDesc}</SelectItem>
            <SelectItem value="created_asc">{copy.admin.sortCreatedAsc}</SelectItem>
            <SelectItem value="remaining_desc">{copy.admin.sortRemainingDesc}</SelectItem>
            <SelectItem value="remaining_asc">{copy.admin.sortRemainingAsc}</SelectItem>
            <SelectItem value="used_desc">{copy.admin.sortUsedDesc}</SelectItem>
            <SelectItem value="used_asc">{copy.admin.sortUsedAsc}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <AdminTable
        columns={columns}
        rows={query.data?.items ?? []}
        rowKey={(t) => t.tenant_id}
        loading={query.isLoading}
        error={query.isError}
        onRetry={() => void query.refetch()}
        minWidth={900}
      />
      <AdminPager offset={offset} limit={LIMIT} total={query.data?.total ?? 0} onOffset={setOffset} />

      <TenantDetailDialog tenantId={detailId} onClose={() => setDetailId(null)} />
    </>
  );
}
