"use client";

import { useRef, useState } from "react";

import { AdminPager, AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/status-badge";
import { TASK_BADGE_STATUS, type AdminRetryReceipt, type AdminTaskFamily, type AdminTaskRow, type AdminTaskStatus } from "@/lib/api/admin-console";
import { useAdminTasks, useAdminTenants, useRetryAdminTask } from "@/lib/api/hooks";
import { errorText } from "@/lib/api/error-text";
import { copy } from "@/lib/copy";

const PAGE_SIZE = 20;
const STATUS_LABEL: Record<AdminTaskStatus, string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消"
};
const FAMILY_LABEL: Record<AdminTaskFamily, string> = {
  video: copy.admin.taskFamilyVideo,
  reverse_prompt: copy.admin.taskFamilyReverse,
  ecom_replicate: copy.admin.taskFamilyEcom
};

// 任务监控（ADMIN-CONSOLE-UI-0001 · FIX1 对齐真契约）：筛选 = 状态/任务类型/租户/时间区间（BE routes:315 有
// status 参数，服务端把 done 归一为 succeeded）。默认聚焦 failed（排障主场景）。重跑按钮以 BE 的 retryable
// 为准（前端不自判 status）。回执披露三态（charged/is_estimate，无 estimate_basis）。
export default function AdminTasksPage() {
  const [status, setStatus] = useState("failed"); // 默认高亮 failed（排障主场景）
  const [family, setFamily] = useState("all");
  const [tenantId, setTenantId] = useState("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [retryTarget, setRetryTarget] = useState<AdminTaskRow | null>(null);
  // 成功回执（FIX1 三态披露）：以 BE 响应为准——charged/is_estimate 分流，横幅明示（不许静默扣费）。
  const [retried, setRetried] = useState<AdminRetryReceipt | null>(null);

  const tenants = useAdminTenants({ sort: "created_at", order: "desc", page: 1, page_size: 100 });
  const query = useAdminTasks({
    status: status === "all" ? "" : (status as AdminTaskStatus),
    task_family: family === "all" ? "" : (family as AdminTaskFamily),
    tenant_id: tenantId === "all" ? undefined : tenantId,
    from: from || undefined,
    to: to || undefined,
    page,
    page_size: PAGE_SIZE
  });
  const retry = useRetryAdminTask();
  // 恰调一次：ref 闸（同 tick 连点 isPending 闭包仍旧值）+ pending 跨渲染闸。
  const retryInFlight = useRef(false);

  const onConfirmRetry = () => {
    if (retryInFlight.current || retry.isPending || !retryTarget) return;
    retryInFlight.current = true;
    retry.mutate(
      { taskId: retryTarget.id, taskFamily: retryTarget.task_family },
      {
        onSettled: () => {
          retryInFlight.current = false;
        },
        onSuccess: (res) => {
          setRetried(res);
          setRetryTarget(null);
        }
      }
    );
  };

  const columns: AdminColumn<AdminTaskRow>[] = [
    { key: "tenant", label: copy.admin.colTenant, render: (r) => <span className="text-ink">{r.tenant_slug}</span> },
    { key: "id", label: copy.admin.colTaskId, render: (r) => <span className="tabular-nums text-ink-soft">{r.id}</span> },
    {
      key: "mode",
      label: copy.admin.colMode,
      render: (r) => (
        <span className="text-ink-soft">
          {FAMILY_LABEL[r.task_family]}
          <span className="ml-1 text-ink-faint">{r.video_mode ?? r.mode}</span>
        </span>
      )
    },
    {
      key: "status",
      label: copy.admin.colStatus,
      render: (r) => (
        <StatusBadge status={TASK_BADGE_STATUS[r.status]} className="px-2 py-0.5 text-[11px]">
          {STATUS_LABEL[r.status]}
        </StatusBadge>
      )
    },
    { key: "progress", label: copy.admin.colProgress, align: "right", render: (r) => (r.progress != null ? `${r.progress}%` : "—") },
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
        r.retryable ? (
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
        <Select value={status} onValueChange={(v) => { setStatus(v); setPage(1); }}>
          <SelectTrigger className="w-[140px]" aria-label={copy.admin.colStatus}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.taskStatusAll}</SelectItem>
            <SelectItem value="failed">失败</SelectItem>
            <SelectItem value="running">运行中</SelectItem>
            <SelectItem value="queued">排队中</SelectItem>
            <SelectItem value="succeeded">已完成</SelectItem>
            <SelectItem value="cancelled">已取消</SelectItem>
          </SelectContent>
        </Select>
        <Select value={family} onValueChange={(v) => { setFamily(v); setPage(1); }}>
          <SelectTrigger className="w-[150px]" aria-label={copy.admin.colTaskFamily}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{copy.admin.taskFamilyAll}</SelectItem>
            <SelectItem value="video">{copy.admin.taskFamilyVideo}</SelectItem>
            <SelectItem value="reverse_prompt">{copy.admin.taskFamilyReverse}</SelectItem>
            <SelectItem value="ecom_replicate">{copy.admin.taskFamilyEcom}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={tenantId} onValueChange={(v) => { setTenantId(v); setPage(1); }}>
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
          <Input type="date" aria-label={copy.admin.usageFrom} value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} className="w-[150px]" />
        </label>
        <label className="flex items-center gap-1 text-[12px] text-ink-soft">
          {copy.admin.usageTo}
          <Input type="date" aria-label={copy.admin.usageTo} value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} className="w-[150px]" />
        </label>
      </div>

      {retry.isError && !retryTarget && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {errorText(retry.error)}
        </p>
      )}
      {/* 成功横幅在表格外（重跑后 retryable=false，行内按钮消失）。FIX1 三态披露（🔴 不许静默扣费）：
          free「不会重复扣费」/ 固定价「将扣费 N」/ 按量「预计约 N，最终按实际成片时长结算」。charged 用 alert+醒目色。 */}
      {retried && (
        <p
          role={retried.charged ? "alert" : "status"}
          className={
            retried.charged
              ? "rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg"
              : "rounded-field bg-glass-soft px-3 py-2 text-[12.5px] text-success-fg"
          }
        >
          {copy.admin.retryDone}（{retried.id}），
          {retried.charged
            ? retried.is_estimate
              ? copy.admin.retryDisclosureEstimate(retried.credits)
              : copy.admin.retryDisclosureFixed(retried.credits)
            : copy.admin.retryDisclosureFree}
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
      <AdminPager page={page} pageSize={PAGE_SIZE} total={query.data?.total ?? 0} onPage={setPage} />

      <ConfirmDialog
        open={!!retryTarget}
        title={copy.admin.retryTitle}
        message={
          <span className="flex flex-col gap-1">
            <span className="tabular-nums text-ink">
              {retryTarget?.id}（{retryTarget?.tenant_slug}）
            </span>
            {/* FIX1：披露字段只在重试回执里（确认前拿不到组合）→ 弹窗做如实的通用口径说明，精确三态在结果横幅。 */}
            <span>{copy.admin.retryConfirmNote}</span>
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
