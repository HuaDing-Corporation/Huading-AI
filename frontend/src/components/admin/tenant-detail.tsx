"use client";

import { useRef, useState } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge } from "@/components/ui/status-badge";
import { TASK_BADGE_STATUS, type AdminTenantRow, type PlanCode } from "@/lib/api/admin-console";
import { useAdjustTenantCredits, useAdminTenantDetail, useChangeTenantPlan, useChangeTenantStatus } from "@/lib/api/hooks";
import { errorText } from "@/lib/api/error-text";
import { useAuth } from "@/lib/auth/auth-context";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";


// 租户详情 + 三个写操作（ADMIN-CONSOLE-UI-0001）。写操作全部：二次确认 + 成功/失败反馈 + 失败展示 BE 中文
// message（errorText 透传 ApiError.message）。余额调整是资金操作——确认弹窗显示「当前 → 调整后」，且
// **确认恰调一次**（isPending 双闸：按钮禁用 + onConfirm 卫语句，参照品牌音色扣费门）。

const fmt = (n: number) => n.toLocaleString("zh-CN");

/** 余额调整块：delta（正负）+ 理由必填 → 确认弹窗（当前 → 调整后，按 subscription.total）→ 恰调一次。
 *  subscription 可空（无生效订阅 → 禁用，BE 也会 404 ACTIVE_SUBSCRIPTION_NOT_FOUND）。 */
function CreditsAdjust({ tenant }: { tenant: AdminTenantRow }) {
  const adjust = useAdjustTenantCredits();
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // 资金操作恰调一次：ref 闸（同一 tick 连点两次时 isPending 闭包仍是旧值，state 闸挡不住——ref 同步生效）。
  const inFlight = useRef(false);

  const currentTotal = tenant.subscription?.total ?? 0;
  const parsed = Number(delta);
  const valid = Number.isInteger(parsed) && parsed !== 0;
  const after = currentTotal + (valid ? parsed : 0);

  const openConfirm = () => {
    setDone(false);
    if (!valid) return setFormError(copy.admin.creditsDeltaRequired);
    if (!reason.trim()) return setFormError(copy.admin.creditsReasonRequired);
    setFormError(null);
    adjust.reset();
    setConfirming(true);
  };
  const onConfirm = () => {
    if (inFlight.current || adjust.isPending) return; // 恰调一次：双闸（ref 同步 + pending 跨渲染）
    inFlight.current = true;
    adjust.mutate(
      { tenantId: tenant.tenant_id, delta: parsed, reason: reason.trim() },
      {
        onSettled: () => {
          inFlight.current = false;
        },
        onSuccess: () => {
          setConfirming(false);
          setDelta("");
          setReason("");
          setDone(true);
        }
      }
    );
  };

  return (
    <section className="flex flex-col gap-2 rounded-field border border-line-gold bg-glass-soft p-3">
      <h3 className="text-[13px] font-semibold text-ink">{copy.admin.creditsAdjust}</h3>
      <label className="text-[12px] text-ink-soft" htmlFor="credits-delta">
        {copy.admin.creditsDelta}
      </label>
      <Input
        id="credits-delta"
        inputMode="numeric"
        value={delta}
        onChange={(e) => setDelta(e.target.value)}
        placeholder={copy.admin.creditsDeltaPlaceholder}
      />
      <label className="text-[12px] text-ink-soft" htmlFor="credits-reason">
        {copy.admin.creditsReason}
      </label>
      <Input
        id="credits-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder={copy.admin.creditsReasonPlaceholder}
      />
      {formError && (
        <p role="alert" className="text-[12px] text-error-fg">
          {formError}
        </p>
      )}
      {done && (
        <p role="status" className="text-[12px] text-success-fg">
          {copy.admin.creditsDone}
        </p>
      )}
      <button
        type="button"
        onClick={openConfirm}
        disabled={!tenant.subscription}
        title={!tenant.subscription ? "该租户没有生效中的订阅" : undefined}
        className="self-start rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep hover:bg-glass-hover disabled:opacity-40"
      >
        {copy.admin.creditsAdjust}
      </button>

      <ConfirmDialog
        open={confirming}
        title={copy.admin.creditsConfirmTitle}
        message={
          <span className="flex flex-col gap-1">
            <span className={cn("font-medium", parsed < 0 ? "text-error-fg" : "text-success-fg")}>
              {parsed >= 0 ? copy.admin.creditsConfirmCharge(parsed) : copy.admin.creditsConfirmDeduct(parsed)}
            </span>
            {/* 资金红线：确认弹窗必须显示 当前余额 → 调整后余额（口径 = subscription.total） */}
            <span className="tabular-nums">{copy.admin.creditsBeforeAfter(currentTotal, after)}</span>
            <span className="text-ink-faint">
              {copy.admin.creditsReason}：{reason.trim()}
            </span>
          </span>
        }
        confirmLabel={copy.admin.creditsConfirmBtn}
        danger={parsed < 0}
        submitting={adjust.isPending}
        error={adjust.isError ? errorText(adjust.error) : null}
        onConfirm={onConfirm}
        onCancel={() => setConfirming(false)}
      />
    </section>
  );
}

/** 改套餐块（free / basic / huading）→ 确认弹窗写明 huading 的能力得失。 */
function PlanChange({ tenant }: { tenant: AdminTenantRow }) {
  const change = useChangeTenantPlan();
  const [plan, setPlan] = useState<PlanCode>((tenant.plan_code as PlanCode) ?? "free");
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState(false);
  const inFlight = useRef(false);

  const onConfirm = () => {
    if (inFlight.current || change.isPending) return;
    inFlight.current = true;
    change.mutate(
      { tenantId: tenant.tenant_id, planCode: plan },
      {
        onSettled: () => {
          inFlight.current = false;
        },
        onSuccess: () => {
          setConfirming(false);
          setDone(true);
        }
      }
    );
  };

  return (
    <section className="flex flex-col gap-2 rounded-field border border-line-gold bg-glass-soft p-3">
      <h3 className="text-[13px] font-semibold text-ink">{copy.admin.planChange}</h3>
      <Select value={plan} onValueChange={(v) => setPlan(v as PlanCode)}>
        <SelectTrigger className="w-full" aria-label={copy.admin.colPlan}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="free">{copy.admin.planFree}</SelectItem>
          <SelectItem value="basic">{copy.admin.planBasic}</SelectItem>
          <SelectItem value="huading">{copy.admin.planHuading}</SelectItem>
        </SelectContent>
      </Select>
      {done && (
        <p role="status" className="text-[12px] text-success-fg">
          {copy.admin.planChangeDone}
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          setDone(false);
          change.reset();
          setConfirming(true);
        }}
        disabled={plan === tenant.plan_code}
        className="self-start rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep hover:bg-glass-hover disabled:opacity-40"
      >
        {copy.admin.planChange}
      </button>
      <ConfirmDialog
        open={confirming}
        title={copy.admin.planChangeTitle}
        message={
          <span className="flex flex-col gap-1">
            <span className="font-medium">{copy.admin.planChangeTo(plan)}</span>
            <span>{copy.admin.planChangeHuadingNote}</span>
          </span>
        }
        confirmLabel={copy.admin.planChangeBtn}
        submitting={change.isPending}
        error={change.isError ? errorText(change.error) : null}
        onConfirm={onConfirm}
        onCancel={() => setConfirming(false)}
      />
    </section>
  );
}

/** 启用/停用块（PATCH {active:bool} → active/suspended）：停用弹窗文字警告（不只靠颜色）；
 *  平台租户自己（= 当前登录租户，后台仅平台可进）的停用按钮置灰（BE 也 422 CANNOT_SUSPEND_PLATFORM_TENANT，双保险）。 */
function StatusToggle({ tenant, isSelf }: { tenant: AdminTenantRow; isSelf: boolean }) {
  const change = useChangeTenantStatus();
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState(false);
  const inFlight = useRef(false);
  const disabling = tenant.status === "active";

  const onConfirm = () => {
    if (inFlight.current || change.isPending) return;
    inFlight.current = true;
    change.mutate(
      { tenantId: tenant.tenant_id, active: !disabling },
      {
        onSettled: () => {
          inFlight.current = false;
        },
        onSuccess: () => {
          setConfirming(false);
          setDone(true);
        }
      }
    );
  };

  const platformBlocked = isSelf && disabling;
  return (
    <section className="flex flex-col gap-2 rounded-field border border-line-gold bg-glass-soft p-3">
      <h3 className="text-[13px] font-semibold text-ink">
        {disabling ? copy.admin.statusDisable : copy.admin.statusEnable}
      </h3>
      {done && (
        <p role="status" className="text-[12px] text-success-fg">
          {copy.admin.statusChangeDone}
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          setDone(false);
          change.reset();
          setConfirming(true);
        }}
        disabled={platformBlocked}
        title={platformBlocked ? copy.admin.statusPlatformProtected : undefined}
        className={cn(
          "self-start rounded-field border px-3 py-1.5 text-[12.5px] disabled:opacity-40",
          disabling
            ? "border-line-gold bg-error-bg text-error-fg hover:bg-error-bg/70"
            : "border-line-gold bg-glass-fill text-gold-deep hover:bg-glass-hover"
        )}
      >
        {disabling ? copy.admin.statusDisable : copy.admin.statusEnable}
      </button>
      {platformBlocked && <p className="text-[11.5px] text-ink-faint">{copy.admin.statusPlatformProtected}</p>}
      <ConfirmDialog
        open={confirming}
        title={disabling ? copy.admin.statusDisableTitle : copy.admin.statusEnableTitle}
        message={disabling ? copy.admin.statusDisableWarn : copy.admin.statusEnableNote}
        confirmLabel={copy.admin.statusChangeBtn}
        danger={disabling}
        submitting={change.isPending}
        error={change.isError ? errorText(change.error) : null}
        onConfirm={onConfirm}
        onCancel={() => setConfirming(false)}
      />
    </section>
  );
}

/** 租户详情弹窗：基础信息 + 最近任务 / 最近用量 / 已挂槽位 + 三个写操作。 */
export function TenantDetailDialog({ tenantId, onClose }: { tenantId: string | null; onClose: () => void }) {
  const detail = useAdminTenantDetail(tenantId);
  const { session } = useAuth();
  const t = detail.data?.tenant;
  return (
    <Dialog open={!!tenantId} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] w-[min(94vw,680px)] overflow-y-auto">
        <DialogTitle className="text-base font-semibold text-ink">{copy.admin.detailTitle}</DialogTitle>
        {detail.isLoading && (
          <DialogDescription className="mt-2 text-[13px] text-ink-soft">{copy.admin.loading}</DialogDescription>
        )}
        {detail.isError && (
          <DialogDescription className="mt-2 text-[13px] text-error-fg">{copy.admin.error}</DialogDescription>
        )}
        {t && (
          <div className="mt-3 flex flex-col gap-4">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-3">
              <div>
                <dt className="text-ink-faint">{copy.admin.colTenant}</dt>
                <dd className="text-ink">
                  {t.name}（{t.slug}）
                </dd>
              </div>
              <div>
                <dt className="text-ink-faint">{copy.admin.colOwner}</dt>
                <dd className="text-ink">{t.owner_email ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">{copy.admin.colPlan}</dt>
                <dd className="text-ink">{t.plan_code ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-ink-faint">{copy.admin.colStatus}</dt>
                <dd>
                  {t.status === "active"
                    ? copy.admin.statusActive
                    : t.status === "closed"
                      ? copy.admin.statusClosed
                      : copy.admin.statusDisabled}
                </dd>
              </div>
              <div className="col-span-2">
                <dt className="text-ink-faint">{copy.admin.colBalance}</dt>
                <dd className="tabular-nums text-ink">
                  {t.subscription ? (
                    <>
                      {fmt(t.subscription.total)} / {fmt(t.subscription.used)} / {fmt(t.subscription.reserved)} /{" "}
                      <b className="text-success-fg">{fmt(t.subscription.remaining)}</b>
                    </>
                  ) : (
                    "—"
                  )}
                </dd>
              </div>
            </dl>

            <div className="grid gap-3 sm:grid-cols-3">
              <CreditsAdjust tenant={t} />
              <PlanChange tenant={t} />
              <StatusToggle tenant={t} isSelf={t.tenant_id === session?.tenantId} />
            </div>

            <section>
              <h3 className="mb-1 text-[13px] font-semibold text-ink">{copy.admin.detailRecentTasks}</h3>
              {detail.data!.recent_tasks.length === 0 ? (
                <p className="text-[12px] text-ink-soft">{copy.admin.empty}</p>
              ) : (
                <ul className="flex flex-col gap-1 text-[12px] text-ink-soft">
                  {detail.data!.recent_tasks.map((task) => (
                    <li key={task.id} className="flex items-center gap-2">
                      <StatusBadge status={TASK_BADGE_STATUS[task.status]} className="px-2 py-0.5 text-[11px]">
                        {task.status}
                      </StatusBadge>
                      <span className="text-ink">{task.id}</span>
                      <span>{task.mode}</span>
                      <span className="text-ink-faint">{task.created_at.slice(0, 10)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <h3 className="mb-1 text-[13px] font-semibold text-ink">{copy.admin.detailRecentUsage}</h3>
              {detail.data!.recent_usage.length === 0 ? (
                <p className="text-[12px] text-ink-soft">{copy.admin.empty}</p>
              ) : (
                <ul className="flex flex-col gap-1 text-[12px] text-ink-soft">
                  {detail.data!.recent_usage.map((u, i) => (
                    <li key={i} className="flex items-center gap-2 tabular-nums">
                      <span className="text-ink-faint">{u.created_at.slice(0, 10)}</span>
                      <span>{u.capability}</span>
                      <span className="text-gold-deep">{fmt(u.credits)} 积分</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <h3 className="mb-1 text-[13px] font-semibold text-ink">{copy.admin.detailVoiceSlots}</h3>
              {detail.data!.voice_slots.length === 0 ? (
                <p className="text-[12px] text-ink-soft">{copy.admin.detailNoSlots}</p>
              ) : (
                <ul className="flex flex-col gap-1 text-[12px] text-ink">
                  {detail.data!.voice_slots.map((s) => (
                    <li key={s.speaker_id} className="tabular-nums">
                      {s.speaker_id}
                      {s.brand_voice_name && <span className="text-ink-soft">（{s.brand_voice_name}）</span>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
