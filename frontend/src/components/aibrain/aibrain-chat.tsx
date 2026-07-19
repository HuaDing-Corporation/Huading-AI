"use client";

// 华鼎AI智脑 · 聊天主壳（AIBRAIN-UI-0001 · FIX1）。左会话列表 / 中消息流 / 下输入框 + 顶部余额 + 充值弹窗。
// 错误分流（对齐 BE 真实 status/code）：402 余额不足 → 弹充值窗；422 超上限 → friendly；502 上游失败 → friendly 重试。

import { useState } from "react";

import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { useConversation, useCreateConversation, useSendMessage, useWallet } from "@/lib/aibrain/hooks";
import { AIBRAIN_ERROR, type IntensityTier, type SendMessageRequest } from "@/lib/aibrain/types";
import { ConversationList } from "@/components/aibrain/conversation-list";
import { MessageStream } from "@/components/aibrain/message-stream";
import { Composer } from "@/components/aibrain/composer";
import { WalletBalance } from "@/components/aibrain/wallet-balance";
import { RechargeDialog } from "@/components/aibrain/recharge-dialog";

export function AibrainChat() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tier, setTier] = useState<IntensityTier>("mid");
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const { data: wallet } = useWallet();
  const balance = wallet?.available_credits ?? 0;
  const create = useCreateConversation();
  const send = useSendMessage();
  const convQuery = useConversation(activeId ?? undefined);
  const messages = convQuery.data?.messages ?? [];

  const handleSend = async (body: SendMessageRequest) => {
    setSendError(null);
    let convId = activeId;
    if (!convId) {
      const conv = await create.mutateAsync();
      convId = conv.id;
      setActiveId(conv.id);
    }
    try {
      await send.mutateAsync({ conversationId: convId, body });
    } catch (err) {
      if (!(err instanceof ApiError)) {
        setSendError(copy.aibrain.error);
        return;
      }
      // 402 余额不足 → 弹充值窗（不是普通报错）。
      if (err.status === 402 || err.code === AIBRAIN_ERROR.INSUFFICIENT_BALANCE) setRechargeOpen(true);
      else if (err.code === AIBRAIN_ERROR.REQUEST_LIMIT_EXCEEDED) setSendError(copy.aibrain.reqLimit);
      else if (err.code === AIBRAIN_ERROR.PROVIDER_FAILED) setSendError(copy.aibrain.providerFailed);
      else setSendError(err.message || copy.aibrain.error);
    }
  };

  return (
    <section className="flex min-h-[calc(100vh-140px)] min-w-0 flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-[18px] font-semibold tracking-wide text-ink">{copy.aibrain.title}</h1>
        <WalletBalance onRecharge={() => setRechargeOpen(true)} />
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
              sending={send.isPending}
              onSend={(body) => void handleSend(body)}
              onInsufficient={() => setRechargeOpen(true)}
            />
          </div>
        </div>
      </div>

      <RechargeDialog open={rechargeOpen} onOpenChange={setRechargeOpen} />
    </section>
  );
}
