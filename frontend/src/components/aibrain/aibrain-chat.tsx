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
  shortfallView,
  type IntensityTier,
  type SendMessageRequest,
  type ShortfallView
} from "@/lib/aibrain/types";
import { ConversationList } from "@/components/aibrain/conversation-list";
import { MessageStream } from "@/components/aibrain/message-stream";
import { Composer } from "@/components/aibrain/composer";
import { WalletBalance } from "@/components/aibrain/wallet-balance";
import { RechargeDialog } from "@/components/aibrain/recharge-dialog";

export function AibrainChat() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tier, setTier] = useState<IntensityTier>("mid");
  const [rechargeOpen, setRechargeOpen] = useState(false);
  // 🔴 402 弹开充值窗时**同时**带上缺口说明（§三）；用户主动点顶部「充值」时为 undefined（无缺口可言）。
  const [shortfall, setShortfall] = useState<ShortfallView | undefined>(undefined);
  const [sendError, setSendError] = useState<string | null>(null);

  const { data: wallet } = useWallet();
  // 🔴 钱包未加载/加载失败时余额是 undefined（**不是 0**）——否则 available<=0 的预检会把有余额的用户也锁死（CR#2）。
  const balance = wallet?.available_credits;

  /** 顶部余额条的「充值」：用户主动来充，**不带**缺口说明（没有被拒的操作，凭空给数字只会吓人）。 */
  const openRecharge = () => {
    setShortfall(undefined);
    setRechargeOpen(true);
  };
  /** 因余额不足被拦/被拒：带缺口说明。预检拦截（`available<=0`）与 BE 402 是同一件事的两个发生点，走同一出口。 */
  const openRechargeWithShortfall = () => {
    setShortfall(shortfallView(tier, balance));
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
      // 🔴 402 余额不足 → 弹充值窗，**并带上缺口说明**（§三）。此前只是默默弹窗，用户看不到
      //    「要多少 / 有多少 / 差多少 / 这是临时预留不是扣费」四件事中的任何一件。
      // ⚠️ 目前 402 只有 AIBRAIN_INSUFFICIENT_BALANCE 一个码。用户已拍板的「余额为负时拒绝新请求」
      //    闸门在 BE 三个分支（#237/#238/#239）里**都还没有实现**，也没有第二个错误码——本包不臆造。
      //    CA 定下码之后，在此处按 code 分出第二条 else-if 即可（缺口说明块换一句「上次透支需补齐」）。
      if (err.status === 402 || err.code === AIBRAIN_ERROR.INSUFFICIENT_BALANCE) openRechargeWithShortfall();
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
              onInsufficient={openRechargeWithShortfall}
            />
          </div>
        </div>
      </div>

      <RechargeDialog open={rechargeOpen} onOpenChange={setRechargeOpen} shortfall={shortfall} />
    </section>
  );
}
