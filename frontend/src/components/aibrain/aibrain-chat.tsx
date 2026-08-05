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
  inflightExposureView,
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

/**
 * 在途敞口 402 的提示文本（FIX2）—— 三段拼接，每段都可能缺席但**「充值不解决」那句永远在**。
 * ① 定性（不是余额问题、充值无效）② 有几条在途（拿得到才说）③ 可重试则说「稍后重试即可」
 * 走内联提示而**不是**充值弹窗：弹窗里有充值按钮，对这个码是错的引导。
 */
function inflightExposureText(detail: unknown): string {
  const { inFlightRequests, retryable } = inflightExposureView(detail);
  return [
    copy.aibrain.inflightExposureTitle,
    inFlightRequests !== undefined ? copy.aibrain.inflightExposureCount(inFlightRequests) : undefined,
    copy.aibrain.inflightExposureNote, // 🔴 这一句无论 detail 有没有都必须出现
    retryable ? copy.aibrain.inflightExposureRetry : undefined
  ]
    .filter(Boolean)
    .join(" ");
}

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
      // ══ 402 有**三种情形**（BE #239 `2a98b5d0`），三套话术互不相容，必须逐码分流 ══════════
      //    · OUTSTANDING_BALANCE       → 上次已答完并交付、实扣超预留 → 欠款，**那笔钱花掉了**
      //    · INSUFFICIENT_BALANCE      → 这次预留不够 → 充值即可，**这笔钱只是临时锁住会退回**
      //    · INFLIGHT_EXPOSURE_LIMIT   → 在途太多 → 🔴**不是余额问题、充值无效**，等前面答完
      //    次序照抄 BE reserve 分支（aibrain.py:901/:912/:927）：负余额 → 预留不足 → 敞口。
      //    前两条走充值窗；**第三条绝不许走充值窗**（走内联提示），否则用户会花钱买一个解决不了的问题。
      if (err.code === AIBRAIN_ERROR.OUTSTANDING_BALANCE) openRechargeFor("outstanding", err.detail);
      else if (err.code === AIBRAIN_ERROR.INSUFFICIENT_BALANCE) openRechargeFor("insufficient", err.detail);
      else if (err.code === AIBRAIN_ERROR.INFLIGHT_EXPOSURE_LIMIT)
        setSendError(inflightExposureText(err.detail));
      // 422：输入太长（BE 在建消息、动钱包**之前**就拦了）——用户可自行解决，讲清怎么做。
      else if (err.code === AIBRAIN_ERROR.PROMPT_LIMIT_EXCEEDED) setSendError(copy.aibrain.promptLimitExceeded);
      // 🔴 503 用量异常冷却（FIX3 第五个码）：**冷却**，与余额、并发都无关 —— 单独一条分流、
      //    单独一套文案。BE 把它判在最前（连提示词闸都在它之后），前端也放在 502 前面，
      //    免得将来有人图省事把它并进 502 那支。
      else if (err.code === AIBRAIN_ERROR.PROVIDER_USAGE_ANOMALY_COOLDOWN)
        setSendError(copy.aibrain.usageAnomalyCooldown);
      // 502：上游用量不可信 → fail-closed 不交付。**零扣费已由源码证实**（见 copy.ts 注释）。
      else if (err.code === AIBRAIN_ERROR.PROVIDER_USAGE_INVALID)
        setSendError(copy.aibrain.providerUsageInvalid);
      else if (err.code === AIBRAIN_ERROR.REQUEST_EXPIRED) setSendError(copy.aibrain.requestExpired);
      else if (err.code === AIBRAIN_ERROR.REQUEST_LIMIT_EXCEEDED) setSendError(copy.aibrain.reqLimit);
      // USAGE_MISSING 与 PROVIDER_FAILED 对用户是同一件事（这次没成、可重试）→ 共用文案。
      else if (err.code === AIBRAIN_ERROR.PROVIDER_FAILED || err.code === AIBRAIN_ERROR.USAGE_MISSING)
        setSendError(copy.aibrain.providerFailed);
      else if (err.code === AIBRAIN_ERROR.ATTACHMENT_NOT_FOUND || err.code === AIBRAIN_ERROR.ATTACHMENT_INVALID)
        setSendError(copy.aibrain.attachmentRejected);
      // 🔴 未知 402 的兜底**从「引导充值」翻转为中性**（FIX2）：三个已知 402 里已经有一个是
      //    「充值无效」，而猜错方向的代价不对称 —— 引导充值猜错 = 用户白花钱（不可逆）；中性猜错
      //    只是让他多点一次顶部那个一直都在的充值入口。故不再弹充值窗。
      else if (err.status === 402) setSendError(copy.aibrain.unknownPaymentIssue);
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

          {/* leading-relaxed：敞口 402 那条会拼到四句（标题 + 条数 + 「充值不解决」+ 重试提示），
              原来的紧行距读起来是一堵墙。其余单句提示不受影响。 */}
          {sendError ? (
            <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[12.5px] leading-relaxed text-error-fg">
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
