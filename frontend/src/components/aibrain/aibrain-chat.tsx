"use client";

// 华鼎AI智脑 · 聊天主壳（AIBRAIN-UI-0001 · FIX1）。左会话列表 / 中消息流 / 下输入框 + 顶部余额 + 充值弹窗。
// 错误分流（对齐 BE 真实 status/code）：402 余额不足 → 弹充值窗；422 超上限 → friendly；502 上游失败 → friendly 重试。

import { useState } from "react";

import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { useConversation, useCreateConversation, useSendMessage, useWallet } from "@/lib/aibrain/hooks";
import {
  AIBRAIN_ERROR,
  outstandingView,
  shortfallView,
  type IntensityTier,
  type SendMessageRequest
} from "@/lib/aibrain/types";
import { ConversationList } from "@/components/aibrain/conversation-list";
import { MessageStream } from "@/components/aibrain/message-stream";
import { Composer } from "@/components/aibrain/composer";
import { WalletBalance } from "@/components/aibrain/wallet-balance";
import { RechargeDialog, type RechargeReason } from "@/components/aibrain/recharge-dialog";

export function AibrainChat() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tier, setTier] = useState<IntensityTier>("mid");
  const [rechargeOpen, setRechargeOpen] = useState(false);
  // 🔴 402 弹开充值窗时**同时**带上说明（§三）；用户主动点顶部「充值」时为 undefined（无缺口可言）。
  //    FIX1：从单一 ShortfallView 换成判别式联合——「预留不足」与「欠费」是两种情形、两套话术。
  const [reason, setReason] = useState<RechargeReason | undefined>(undefined);
  const [sendError, setSendError] = useState<string | null>(null);

  const { data: wallet } = useWallet();
  // 🔴 钱包未加载/加载失败时余额是 undefined（**不是 0**）——否则 available<=0 的预检会把有余额的用户也锁死（CR#2）。
  const balance = wallet?.available_credits;

  /** 顶部余额条的「充值」：用户主动来充，**不带**说明块（没有被拒的操作，凭空给数字只会吓人）。 */
  const openRecharge = () => {
    setReason(undefined);
    setRechargeOpen(true);
  };
  /**
   * 因余额问题被拦/被拒 → 弹充值窗并说清是哪一种。
   * 预检拦截与 BE 402 是同一件事的两个发生点，走同一出口；区别只在预检时**没有响应体**
   * （`detail` 为 undefined）→ `shortfallView` 自动落到下界回退、`outstandingView` 自动落到钱包回退。
   */
  const openRechargeFor = (kind: "insufficient" | "outstanding", detail?: unknown) => {
    setReason(
      kind === "outstanding"
        ? { kind, outstanding: outstandingView(detail, balance) }
        : { kind, shortfall: shortfallView(tier, balance, detail) }
    );
    setRechargeOpen(true);
  };

  const create = useCreateConversation();
  const send = useSendMessage();
  const convQuery = useConversation(activeId ?? undefined);
  const messages = convQuery.data?.messages ?? [];
  const busy = send.isPending || create.isPending; // 建会话在途也禁用发送，防双建（CR#7）

  // 🔴 P1-1：返回是否**发送成功**——composer 据此决定清不清空（失败保留文字/附件/预览）。
  const handleSend = async (body: SendMessageRequest): Promise<boolean> => {
    setSendError(null);
    try {
      // 建会话与发消息**同在 try 内**：首条消息若建会话失败，也走错误分流、不静默丢消息（CR#1）。
      let convId = activeId;
      if (!convId) {
        const conv = await create.mutateAsync();
        convId = conv.id;
        setActiveId(conv.id);
      }
      await send.mutateAsync({ conversationId: convId, body });
      return true;
    } catch (err) {
      if (!(err instanceof ApiError)) {
        setSendError(copy.aibrain.error);
        return false;
      }
      // 🔴 402 有**两种情形**（BE PR #239 `e2bc2c02`），必须分开，因为两套话术是相反的：
      //    · OUTSTANDING_BALANCE → 上次已答完并交付、实际用量超预留 → 欠款，**那笔钱花掉了**
      //    · INSUFFICIENT_BALANCE → 这次预留不够 → 充值即可，**这笔钱只是临时锁住**
      //    欠费判在前（与 BE reserve 分支的判定次序一致），否则欠费用户会收到一句不相干的
      //    「结束后差额会退回」——他上次的钱早就退不回来了。
      // ⚠️ `err.status === 402` 的兜底放在**最后**：新码（如 #239 还在加的「租户级聚合在途敞口」
      //    闸门，`e2bc2c02` 里尚未实现）会落到这里，按「预留不足」展示——那是较温和的一种说法，
      //    不会把没欠费的人说成欠费。
      if (err.code === AIBRAIN_ERROR.OUTSTANDING_BALANCE) openRechargeFor("outstanding", err.detail);
      else if (err.code === AIBRAIN_ERROR.INSUFFICIENT_BALANCE || err.status === 402)
        openRechargeFor("insufficient", err.detail);
      else if (err.code === AIBRAIN_ERROR.REQUEST_EXPIRED) setSendError(copy.aibrain.requestExpired);
      else if (err.code === AIBRAIN_ERROR.REQUEST_LIMIT_EXCEEDED) setSendError(copy.aibrain.reqLimit);
      else if (err.code === AIBRAIN_ERROR.PROVIDER_FAILED) setSendError(copy.aibrain.providerFailed);
      else if (err.code === AIBRAIN_ERROR.ATTACHMENT_NOT_FOUND || err.code === AIBRAIN_ERROR.ATTACHMENT_INVALID)
        setSendError(copy.aibrain.attachmentRejected);
      else setSendError(err.message || copy.aibrain.error);
      return false;
    }
  };

  return (
    <section className="flex min-h-[calc(100vh-140px)] min-w-0 flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold tracking-wide text-ink">{copy.aibrain.title}</h1>
        <WalletBalance onRecharge={openRecharge} />
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 sm:flex-row">
        <ConversationList activeId={activeId} onSelect={setActiveId} />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col rounded-card border border-line-gold bg-glass-soft p-4">
          {convQuery.isError ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
              <p role="alert" className="text-[13px] text-error-fg">{copy.aibrain.loadError}</p>
              <Button variant="soft" size="sm" onClick={() => void convQuery.refetch()}>{copy.aibrain.retry}</Button>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
              <MessageStream messages={messages} pending={send.isPending} />
            </div>
          )}

          {sendError ? (
            <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
              {sendError}
            </p>
          ) : null}

          <div className="mt-3">
            <Composer
              tier={tier}
              onTierChange={setTier}
              balance={balance}
              sending={busy}
              onSend={handleSend}
              onInsufficient={openRechargeFor}
            />
          </div>
        </div>
      </div>

      <RechargeDialog open={rechargeOpen} onOpenChange={setRechargeOpen} reason={reason} />
    </section>
  );
}
