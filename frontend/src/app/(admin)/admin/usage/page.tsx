"use client";

import { useState } from "react";
import { Download } from "lucide-react";

import { AdminPager, AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { exportAdminUsageCsv, type AdminUsageRow } from "@/lib/api/admin-console";
import { useAdminTenants, useAdminUsage } from "@/lib/api/hooks";
import { errorText } from "@/lib/api/error-text";
import { yuan } from "@/lib/analytics/format";
import { copy } from "@/lib/copy";

const LIMIT = 20;
const fmt = (n: number) => n.toLocaleString("zh-CN");

// 计费/用量明细（ADMIN-CONSOLE-UI-0001 §二.3）：租户/时间/capability/provider/状态筛选 + 分页 + CSV 导出。
// 导出走 BE export 端点（BE 有行数上限，超限 422 的中文 message 原样展示）。
export default function AdminUsagePage() {
  const [tenantId, setTenantId] = useState("all");
  const [capability, setCapability] = useState("all");
  const [provider, setProvider] = useState("all");
  const [status, setStatus] = useState("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportDone, setExportDone] = useState(false);

  const tenants = useAdminTenants({ sort: "created_desc", limit: 100, offset: 0 });
  const filters = {
    tenant_id: tenantId === "all" ? undefined : tenantId,
    capability: capability === "all" ? undefined : capability,
    provider: provider === "all" ? undefined : provider,
    status: status === "all" ? undefined : status,
    from: from || undefined,
    to: to || undefined
  };
  const query = useAdminUsage({ ...filters, limit: LIMIT, offset });

  const onExport = async () => {
    if (exporting) return;
    setExporting(true);
    setExportError(null);
    setExportDone(false);
    try {
      const blob = await exportAdminUsageCsv(filters);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "usage-export.csv";
      a.click();
      URL.revokeObjectURL(url);
      setExportDone(true);
    } catch (err) {
      // BE 行数上限 422 等 → 中文 message 原样展示
      setExportError(errorText(err));
    } finally {
      setExporting(false);
    }
  };

  const columns: AdminColumn<AdminUsageRow>[] = [
    { key: "time", label: copy.admin.colTime, render: (r) => <span className="text-ink-faint tabular-nums">{r.created_at.replace("T", " ").slice(0, 16)}</span> },
    { key: "tenant", label: copy.admin.colTenant, render: (r) => <span className="text-ink">{r.tenant_slug}</span> },
    { key: "capability", label: copy.admin.colCapability, render: (r) => <span className="text-ink-soft">{r.capability}</span> },
    { key: "provider", label: copy.admin.colProviderModel, render: (r) => <span className="text-ink-soft">{r.provider}{r.model ? ` / ${r.model}` : ""}</span> },
    { key: "quantity", label: copy.admin.colQuantity, align: "right", render: (r) => `${fmt(r.quantity)} ${r.unit}` },
    { key: "credits", label: copy.admin.colCredits, align: "right", render: (r) => <span className="text-gold-deep">{fmt(r.credits)}</span> },
    { key: "cost", label: copy.admin.colCost, align: "right", render: (r) => yuan(r.cost_cents) },
    { key: "status", label: copy.admin.colUsageStatus, render: (r) => <span className="text-ink-soft">{r.status}</span> },
    { key: "task", label: copy.admin.colTask, render: (r) => <span className="tabular-nums text-ink-faint">{r.task_id ?? "—"}</span> }
  ];

  const resetPage = () => setOffset(0);

  return (
    <>
      <header className="px-1">
        <h1 className="text-[22px] font-semibold tracking-[0.5px] text-ink">{copy.admin.usageTitle}</h1>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={tenantId} onValueChange={(v) => { setTenantId(v); resetPage(); }}>
          <SelectTrigger className="w-[180px]" aria-label={copy.admin.colTenant}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.usageTenantAll}</SelectItem>
            {(tenants.data?.items ?? []).map((t) => (
              <SelectItem key={t.tenant_id} value={t.tenant_id}>{t.name}（{t.slug}）</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={capability} onValueChange={(v) => { setCapability(v); resetPage(); }}>
          <SelectTrigger className="w-[170px]" aria-label={copy.admin.colCapability}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.usageCapabilityAll}</SelectItem>
            <SelectItem value="video_generate">video_generate</SelectItem>
            <SelectItem value="image_generate">image_generate</SelectItem>
            <SelectItem value="copywriting">copywriting</SelectItem>
            <SelectItem value="voice_clone">voice_clone</SelectItem>
          </SelectContent>
        </Select>
        <Select value={provider} onValueChange={(v) => { setProvider(v); resetPage(); }}>
          <SelectTrigger className="w-[150px]" aria-label={copy.admin.colProviderModel}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.usageProviderAll}</SelectItem>
            <SelectItem value="seedance">seedance</SelectItem>
            <SelectItem value="apimart">apimart</SelectItem>
            <SelectItem value="deepseek">deepseek</SelectItem>
            <SelectItem value="doubao">doubao</SelectItem>
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={(v) => { setStatus(v); resetPage(); }}>
          <SelectTrigger className="w-[140px]" aria-label={copy.admin.colUsageStatus}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.usageStatusAll}</SelectItem>
            <SelectItem value="reserved">reserved</SelectItem>
            <SelectItem value="settled">settled</SelectItem>
            <SelectItem value="released">released</SelectItem>
          </SelectContent>
        </Select>
        <label className="flex items-center gap-1 text-[12px] text-ink-soft">
          {copy.admin.usageFrom}
          <Input type="date" aria-label={copy.admin.usageFrom} value={from} onChange={(e) => { setFrom(e.target.value); resetPage(); }} className="w-[150px]" />
        </label>
        <label className="flex items-center gap-1 text-[12px] text-ink-soft">
          {copy.admin.usageTo}
          <Input type="date" aria-label={copy.admin.usageTo} value={to} onChange={(e) => { setTo(e.target.value); resetPage(); }} className="w-[150px]" />
        </label>
        <button
          type="button"
          onClick={() => void onExport()}
          disabled={exporting}
          className="ml-auto inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep hover:bg-glass-hover disabled:opacity-50"
        >
          <Download size={14} strokeWidth={2} aria-hidden />
          {exporting ? copy.common.processing : copy.admin.exportCsv}
        </button>
      </div>
      {exportError && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {exportError}
        </p>
      )}
      {exportDone && (
        <p role="status" className="text-[12px] text-success-fg">
          {copy.admin.exportDone}
        </p>
      )}

      <AdminTable
        columns={columns}
        rows={query.data?.items ?? []}
        rowKey={(r) => r.id}
        loading={query.isLoading}
        error={query.isError}
        onRetry={() => void query.refetch()}
        minWidth={980}
      />
      <AdminPager offset={offset} limit={LIMIT} total={query.data?.total ?? 0} onOffset={setOffset} />
    </>
  );
}
