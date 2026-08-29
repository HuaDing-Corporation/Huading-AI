"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errorText } from "@/lib/api/error-text";
import {
  getAdminBrandVoiceOrder,
  listAdminBrandVoiceOrders,
  resolveAdminBrandVoiceOrder,
  type AdminBrandVoiceOrderRead,
  type AdminBrandVoiceOrderStatus
} from "@/lib/api/admin-console";

function isPlatform403(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && error.status === 403;
}

const ORDER_STATUS_LABELS: Record<AdminBrandVoiceOrderStatus, string> = {
  awaiting_fulfillment: "等待人工交付",
  fulfilled: "已交付",
  rejected: "已拒绝"
};

export default function AdminBrandVoiceOrdersPage() {
  const [status, setStatus] = useState<AdminBrandVoiceOrderStatus | "">("awaiting_fulfillment");
  const [items, setItems] = useState<AdminBrandVoiceOrderRead[]>([]);
  const [selected, setSelected] = useState<AdminBrandVoiceOrderRead | null>(null);
  const [providerVoiceId, setProviderVoiceId] = useState("");
  const [rejectionReason, setRejectionReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listAdminBrandVoiceOrders({ status, page: 1, page_size: 20 });
      setItems(page.items);
      setForbidden(false);
    } catch (caught) {
      if (isPlatform403(caught)) setForbidden(true);
      else setError(errorText(caught));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { void load(); }, [load]);

  const openDetail = async (order: AdminBrandVoiceOrderRead) => {
    setDetailLoading(true);
    setError(null);
    try {
      setSelected(await getAdminBrandVoiceOrder(order.id));
      setProviderVoiceId("");
      setRejectionReason("");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setDetailLoading(false);
    }
  };

  const resolve = async (action: "fulfill" | "reject") => {
    if (!selected || selected.status !== "awaiting_fulfillment" || submitting) return;
    const provider = providerVoiceId.trim();
    const reason = rejectionReason.trim();
    if (action === "fulfill" && !provider) return setError("请输入豆包音色 ID");
    if (action === "reject" && !reason) return setError("请输入拒绝原因");
    setError(null);
    setSubmitting(true);
    try {
      const result = await resolveAdminBrandVoiceOrder(
        selected.id,
        action === "fulfill"
          ? { action: "fulfill", provider_voice_id: provider }
          : { action: "reject", rejection_reason: reason }
      );
      setSelected(result);
      setItems((current) => current.map((item) => item.id === result.id ? result : item));
    } catch (caught) {
      setError(errorText(caught));
      if (typeof caught === "object" && caught !== null && "status" in caught && caught.status === 409) {
        try { setSelected(await getAdminBrandVoiceOrder(selected.id)); } catch { /* keep the conflict visible */ }
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (forbidden) {
    return <div role="alert" className="rounded-field border border-line-gold bg-error-bg p-4 text-[13px] text-error-fg">仅平台管理员可访问人工音色订单</div>;
  }

  return (
    <>
      <header className="flex min-w-0 flex-wrap items-end justify-between gap-3 px-1">
        <div>
          <h1 className="text-[22px] font-semibold tracking-[.5px] text-ink">人工音色订单</h1>
          <p className="mt-1 text-[12.5px] text-ink-soft">按需读取授权音频；交付与拒绝互斥且终态不可重复操作。</p>
        </div>
        <label className="text-[12px] text-ink-soft">
          状态
          <select
            aria-label="订单状态"
            value={status}
            onChange={(event) => setStatus(event.target.value as AdminBrandVoiceOrderStatus | "")}
            className="ml-2 rounded-field border border-line-gold bg-glass-fill px-2 py-1.5 text-ink"
          >
            <option value="">全部</option>
            <option value="awaiting_fulfillment">等待人工交付</option>
            <option value="fulfilled">已交付</option>
            <option value="rejected">已拒绝</option>
          </select>
        </label>
      </header>

      {error && <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">{error}</p>}

      <section className="overflow-hidden rounded-field border border-line-gold bg-glass-fill">
        {loading ? <p className="p-4 text-[13px] text-ink-soft">正在加载订单…</p> : items.length === 0 ? (
          <p className="p-4 text-[13px] text-ink-soft">当前筛选下暂无订单</p>
        ) : (
          <ul className="divide-y divide-line-gold/70">
            {items.map((order) => (
              <li key={order.id} className="flex min-w-0 flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-[12rem] flex-1">
                  <p className="break-words text-[13px] font-medium text-ink">{order.requested_name}</p>
                  <div className="mt-1 flex min-w-0 flex-col gap-0.5 text-[11.5px] text-ink-faint sm:flex-row sm:flex-wrap sm:items-center sm:gap-1">
                    <span className="break-all">租户 {order.tenant_id} · 用户 {order.ordered_by_user_id}</span>
                    <span className="hidden sm:inline" aria-hidden>·</span>
                    <time className="whitespace-nowrap tabular-nums" dateTime={order.created_at}>{new Date(order.created_at).toLocaleString()}</time>
                  </div>
                </div>
                <span className="text-[12px] text-ink-soft">{ORDER_STATUS_LABELS[order.status]}</span>
                <Button size="sm" variant="soft" onClick={() => void openDetail(order)} disabled={detailLoading}>查看订单</Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {selected && (
        <section className="grid min-w-0 gap-4 rounded-field border border-line-gold bg-glass-soft p-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)]">
          <div className="min-w-0 space-y-2">
            <h2 className="text-[15px] font-semibold text-ink">{selected.requested_name}</h2>
            <p className="break-all text-[12px] text-ink-soft">订单 {selected.id}</p>
            {selected.source_audio_url && <audio aria-label="订单源音频" controls src={selected.source_audio_url} className="w-full" />}
            {selected.status === "fulfilled" && (
              <div role="status" className="space-y-1 text-[13px] text-success-fg">
                <p>已交付并结算 {selected.billing.settled_credits} 积分</p>
                {selected.expires_at && <p className="text-ink-soft">到期时间：{new Date(selected.expires_at).toLocaleString()}</p>}
              </div>
            )}
            {selected.status === "rejected" && <p role="status" className="text-[13px] text-error-fg">已拒绝：{selected.rejection_reason}</p>}
          </div>
          <div className="min-w-0 space-y-3">
            <label htmlFor="provider-voice-id" className="block text-[12px] text-ink-soft">豆包音色 ID</label>
            <Input id="provider-voice-id" value={providerVoiceId} onChange={(event) => setProviderVoiceId(event.target.value)} />
            <Button className="w-full" onClick={() => void resolve("fulfill")} disabled={submitting || selected.status !== "awaiting_fulfillment"}>确认交付</Button>
            <label htmlFor="rejection-reason" className="block text-[12px] text-ink-soft">拒绝原因</label>
            <Input id="rejection-reason" value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} />
            <Button variant="soft" className="w-full" onClick={() => void resolve("reject")} disabled={submitting || selected.status !== "awaiting_fulfillment"}>拒绝订单</Button>
          </div>
        </section>
      )}
    </>
  );
}
