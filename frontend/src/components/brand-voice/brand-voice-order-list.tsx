"use client";

import { RefreshCw } from "lucide-react";

import { Card, CardTitle } from "@/components/ui/card";
import { useBrandVoiceOrders } from "@/lib/api/hooks";
import type { BrandVoiceOrderRead } from "@/lib/api/brand-voice-orders";

function rejectedCopy(order: BrandVoiceOrderRead): string {
  if (order.refund_disposition === "source_subscription_released") {
    return "原订阅冻结已释放，积分现在可用";
  }
  if (order.refund_disposition === "current_subscription_credited") {
    return "已退回当前订阅，积分现在可用";
  }
  if (order.refund_disposition === "pending_next_subscription" && order.refund_grant_status === "pending") {
    return "退款将在下次订阅激活时到账";
  }
  return "退款状态以订单最新查询结果为准";
}

function OrderStatus({ order }: { order: BrandVoiceOrderRead }) {
  if (order.status === "awaiting_fulfillment") {
    return (
      <p className="mt-1 text-[12.5px] leading-5 text-queue-fg">
        已冻结 {order.billing.held_credits} 积分，等待平台人工交付；订单不自动超时且无法取消
      </p>
    );
  }
  if (order.status === "fulfilled") {
    return (
      <div className="mt-1 space-y-1 text-[12.5px] leading-5 text-success-fg">
        <p>已交付并结算 {order.billing.settled_credits} 积分</p>
        {order.fulfilled_at && <p className="text-ink-soft">交付时间：{new Date(order.fulfilled_at).toLocaleString()}</p>}
        {order.expires_at && <p className="text-ink-soft">到期时间：{new Date(order.expires_at).toLocaleString()}</p>}
      </div>
    );
  }
  return (
    <div className="mt-1 space-y-1 text-[12.5px] leading-5">
      <p className="text-error-fg">订单已拒绝{order.rejection_reason ? `：${order.rejection_reason}` : ""}</p>
      <p className="text-ink-soft">{rejectedCopy(order)}</p>
    </div>
  );
}

export function BrandVoiceOrderList() {
  const orders = useBrandVoiceOrders();
  const items = orders.data ?? [];

  return (
    <Card animateIn>
      <div className="mb-3 flex min-w-0 items-center justify-between gap-3">
        <CardTitle>人工开通订单</CardTitle>
        <button
          type="button"
          aria-label="查询退款状态"
          onClick={() => void orders.refetch()}
          className="inline-flex flex-none items-center gap-1 rounded-field px-2 py-1 text-[12px] text-gold-deep hover:bg-glass-hover"
        >
          <RefreshCw size={13} aria-hidden /> 刷新
        </button>
      </div>
      {orders.isLoading ? (
        <p className="text-[13px] text-ink-soft">正在加载订单…</p>
      ) : orders.isError ? (
        <p role="alert" className="text-[13px] text-error-fg">订单暂时无法加载，请重试</p>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-ink-soft">暂无人工开通订单</p>
      ) : (
        <ul className="space-y-2">
          {items.map((order) => (
            <li key={order.id} className="min-w-0 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 break-words text-[13px] font-medium text-ink">{order.requested_name}</span>
                <span className="flex-none text-[11.5px] text-ink-faint">{order.order_type === "renew" ? "续期" : "新开"}</span>
              </div>
              <OrderStatus order={order} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
