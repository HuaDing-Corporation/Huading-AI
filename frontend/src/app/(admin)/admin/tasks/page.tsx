"use client";

import { useRef, useState } from "react";

import { AdminPager, AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/status-badge";
import type { AdminTaskRow } from "@/lib/api/admin-console";
import { useAdminTasks, useAdminTenants, useRetryAdminTask } from "@/lib/api/hooks";
import { errorText } from "@/lib/api/error-text";
import { copy } from "@/lib/copy";

const LIMIT = 20;
const STATUS_LABEL: Record<AdminTaskRow["status"], string> = {
  queued: "排队中",
  running: "运行中",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消"
};

// 任务监控（ADMIN-CONSOLE-UI-0001 §二.4）：默认聚焦 failed；错误码+错误信息可见；重跑失败任务。
// 🔴 重跑扣费口径以 BE 回执为准：冻结文档定「首版重跑不重复扣费」→ 确认弹窗写明「不会重复扣费」；
// 若 BE 对某类任务返回 charged:true，改用 retryChargeNote 明示扣多少积分——**不许静默扣费**。
export default function AdminTasksPage() {
  const [status, setStatus] = useState("failed"); // 默认高亮 failed（排障主场景）
  const [tenantId, setTenantId] = useState("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [retryTarget, setRetryTarget] = useState<AdminTaskRow | null>(null);
  // 成功回执（含扣费披露）：以 BE 响应为准——charged:true 时横幅必须明示扣费（不许静默扣费）。
  const [retried, setRetried] = useState<{ id: string; charged: boolean; credits?: number } | null>(null);

  const tenants = useAdminTenants({ sort: "created_desc", limit: 100, offset: 0 });
  const query = useAdminTasks({
    status: status === "all" ? undefined : status,
    tenant_id: tenantId === "all" ? undefined : tenantId,
    from: from || undefined,
    to: to || undefined,
    limit: LIMIT,
    offset
  });
  const retry = useRetryAdminTask();
  // 恰调一次：ref 闸（同 tick 连点 isPending 闭包仍旧值）+ pending 跨渲染闸。
  const retryInFlight = useRef(false);

  const onConfirmRetry = () => {
    if (retryInFlight.current || retry.isPending || !retryTarget) return;
    retryInFlight.current = true;
    retry.mutate(retryTarget.id, {
      onSettled: () => {
        retryInFlight.current = false;
      },
      onSuccess: (res) => {
        // 以 BE 回执为准：charged:true（口径变更）→ 横幅披露实际扣费。
        setRetried({ id: res.task_id, charged: res.charged, credits: res.charge_credits });
        setRetryTarget(null);
      }
    });
  };

  const columns: AdminColumn<AdminTaskRow>[] = [
    { key: "tenant", label: copy.admin.colTenant, render: (r) => <span className="text-ink">{r.tenant_slug}</span> },
    { key: "id", label: copy.admin.colTaskId, render: (r) => <span className="tabular-nums text-ink-soft">{r.id}</span> },
    { key: "mode", label: copy.admin.colMode, render: (r) => <span className="text-ink-soft">{r.mode}</span> },
    {
      key: "status",
      label: copy.admin.colStatus,
      render: (r) => (
        <StatusBadge status={r.status} className="px-2 py-0.5 text-[11px]">
          {STATUS_LABEL[r.status]}
        </StatusBadge>
      )
    },
    { key: "progress", label: copy.admin.colProgress, align: "right", render: (r) => `${r.progress}%` },
    {
      key: "error",
      label: copy.admin.colError,
      render: (r) =>
        r.error_code ? (
          <span className="text-error-fg">
            <b>{r.error_code}</b>
            {r.error_message && <span className="ml-1 text-[12px]">{r.error_message}</span>}
          </span>
        ) : (
          <span className="text-ink-faint">—</span>
        )
    },
    {
      key: "times",
      label: copy.admin.colTimes,
      render: (r) => (
        <span className="tabular-nums text-[11.5px] text-ink-faint">
          {r.created_at.replace("T", " ").slice(0, 16)}
          {r.started_at ? ` / ${r.started_at.replace("T", " ").slice(11, 16)}` : " / —"}
          {r.finished_at ? ` / ${r.finished_at.replace("T", " ").slice(11, 16)}` : " / —"}
        </span>
      )
    },
    {
      key: "duration",
      label: copy.admin.colDuration,
      align: "right",
      render: (r) => (r.duration_seconds != null ? `${r.duration_seconds}s` : "—")
    },
    {
      key: "actions",
      label: copy.admin.colActions,
      render: (r) =>
        r.status === "failed" ? (
          <button
            type="button"
            onClick={() => {
              retry.reset();
              setRetried(null);
              setRetryTarget(r);
            }}
            className="rounded-field border border-line-gold bg-glass-fill px-2.5 py-1 text-[12px] text-gold-deep hover:bg-glass-hover"
          >
            {copy.admin.retryTask}
          </button>
        ) : null
    }
  ];

  return (
    <>
      <header className="px-1">
        <h1 className="text-[22px] font-semibold tracking-[0.5px] text-ink">{copy.admin.tasksTitle}</h1>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={status} onValueChange={(v) => { setStatus(v); setOffset(0); }}>
          <SelectTrigger className="w-[140px]" aria-label={copy.admin.colStatus}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.taskStatusAll}</SelectItem>
            <SelectItem value="failed">失败</SelectItem>
            <SelectItem value="running">运行中</SelectItem>
            <SelectItem value="queued">排队中</SelectItem>
            <SelectItem value="done">已完成</SelectItem>
            <SelectItem value="cancelled">已取消</SelectItem>
          </SelectContent>
        </Select>
        <Select value={tenantId} onValueChange={(v) => { setTenantId(v); setOffset(0); }}>
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
        <label className="flex items-center gap-1 text-[12px] text-ink-soft">
          {copy.admin.usageFrom}
          <Input type="date" aria-label={copy.admin.usageFrom} value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0); }} className="w-[150px]" />
        </label>
        <label className="flex items-center gap-1 text-[12px] text-ink-soft">
          {copy.admin.usageTo}
          <Input type="date" aria-label={copy.admin.usageTo} value={to} onChange={(e) => { setTo(e.target.value); setOffset(0); }} className="w-[150px]" />
        </label>
      </div>

      {retry.isError && !retryTarget && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {errorText(retry.error)}
        </p>
      )}
      {/* 成功横幅在表格外：重跑后任务回 queued，会从 failed 筛选中消失——行内提示会随行一起没了。
          扣费披露：charged:true 用 alert 角色 + 醒目色（资金变动不许静默）。 */}
      {retried && (
        <p
          role={retried.charged ? "alert" : "status"}
          className={
            retried.charged
              ? "rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg"
              : "rounded-field bg-glass-soft px-3 py-2 text-[12.5px] text-success-fg"
          }
        >
          {copy.admin.retryDone}（{retried.id}）
          {retried.charged ? copy.admin.retryChargedSuffix(retried.credits ?? 0) : ""}
        </p>
      )}

      <AdminTable
        columns={columns}
        rows={query.data?.items ?? []}
        rowKey={(r) => r.id}
        loading={query.isLoading}
        error={query.isError}
        onRetry={() => void query.refetch()}
        minWidth={1020}
      />
      <AdminPager offset={offset} limit={LIMIT} total={query.data?.total ?? 0} onOffset={setOffset} />

      <ConfirmDialog
        open={!!retryTarget}
        title={copy.admin.retryTitle}
        message={
          <span className="flex flex-col gap-1">
            <span className="tabular-nums text-ink">
              {retryTarget?.id}（{retryTarget?.tenant_slug}）
            </span>
            {/* 冻结文档口径：首版重跑不重复扣费（BE charged:false）。BE 若改口径，此处切 retryChargeNote。 */}
            <span>{copy.admin.retryFreeNote}</span>
          </span>
        }
        confirmLabel={copy.admin.retryBtn}
        submitting={retry.isPending}
        error={retry.isError ? errorText(retry.error) : null}
        onConfirm={onConfirmRetry}
        onCancel={() => setRetryTarget(null)}
      />
    </>
  );
}
