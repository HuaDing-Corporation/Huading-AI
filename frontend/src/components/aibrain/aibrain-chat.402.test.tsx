import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/lib/aibrain/types";

// ── PRICING-UI-0001-FIX1 · **402 分流**承重（CB 复审第 1 点：aibrain-chat.tsx 把所有 402 当成
//    普通预留不足，缺 `AIBRAIN_OUTSTANDING_BALANCE` 分流）───────────────────────────────────────
//
// 🔴 这个文件是**变异证伪抓出来的补漏**，值得写明经过：
//    pricing-disclosure.test.tsx 的门D 只验了「给 RechargeDialog 一个 outstanding reason，它渲染得对」，
//    **没有**验「AibrainChat 收到 OUTSTANDING 这个码时，会不会把 reason 传对」。于是我做变异
//    M5（删掉 chat 里的欠费分流、所有 402 一律当预留不足 = 退回 CB 指出的那个缺陷）时，**全组绿**。
//    组件级的门守不住分流逻辑 —— 分流发生在 chat 里，门就得开在 chat 上。
//
// 覆盖矩阵（每条都从「真的抛一个 ApiError」出发，走完整分流链路到弹窗文案）：
//    OUTSTANDING 402  → 欠费话术，且**不出现**「临时预留」
//    INSUFFICIENT 402 → 预留不足话术 + BE detail 的精确数字
//    未知码的 402     → 落到较温和的「预留不足」兜底（不把没欠费的人说成欠费）
//    预检拦截（余额为负 / 余额为 0）→ 不发请求，但弹窗仍要说对是哪一种

const hooks = vi.hoisted(() => ({
  sendMutateAsync: vi.fn(),
  wallet: { available_credits: 0 } as { available_credits: number } | undefined,
  conversationMessages: [] as ChatMessage[],
  conversationError: false
}));

vi.mock("@/lib/aibrain/hooks", () => ({
  useWallet: () => ({ data: hooks.wallet }),
  useCreateConversation: () => ({ mutateAsync: vi.fn().mockResolvedValue({ id: "conv-1" }), isPending: false }),
  useSendMessage: () => ({ mutateAsync: hooks.sendMutateAsync, isPending: false }),
  useConversation: () => ({
    data: { messages: hooks.conversationMessages },
    isError: hooks.conversationError,
    refetch: vi.fn()
  }),
  // ⚠️ `useConversations()` 的 data **就是数组**（conversation-list.tsx:31 `const items = data ?? []`），
  //    不是 `{items,total}` 信封 —— 按信封写会得到 `items.map is not a function`。
  useConversations: () => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() }),
  useDeleteConversation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useClearConversations: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTopup: () => ({ mutateAsync: vi.fn(), isPending: false })
}));

import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";
import { AibrainChat } from "./aibrain-chat";

/** BE 两类 402 的真实形状（aibrain.py:561-567 / :574-583）。 */
const OUTSTANDING = new ApiError(
  "Pay the outstanding AIBRAIN balance before starting new work.",
  "AIBRAIN_OUTSTANDING_BALANCE",
  402,
  { available_credits: -42.5, outstanding_credits: 42.5 }
);
const INSUFFICIENT = new ApiError(
  "Insufficient reasoning balance for this request (required 137.6256, available 20).",
  "AIBRAIN_INSUFFICIENT_BALANCE",
  402,
  { required_credits: 137.6256, available_credits: 20, shortfall_credits: 117.6256, temporary_reservation: true }
);

/** 壳里仍有未被本文件 mock 覆盖的 useQuery 调用者 → 统一套 Provider（retry 关掉，免得失败态被重试掩盖）。 */
function renderChat() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AibrainChat />
    </QueryClientProvider>
  );
}

const dialog = () => screen.getByRole("dialog");
async function typeAndSend(text = "你好") {
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText(/输入问题/), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: copy.aibrain.send }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  hooks.wallet = { available_credits: 500 }; // 正余额 → 预检放行，让请求真发出去，由 BE 的码分流
  hooks.conversationMessages = [];
  hooks.conversationError = false;
  hooks.sendMutateAsync.mockResolvedValue({});
});

describe("详情后台刷新失败", () => {
  it("已有缓存回答时继续显示消息，不用阻断式加载错误替换消息流", () => {
    hooks.conversationMessages = [
      {
        id: "message-assistant-cached",
        conversation_id: "conv-1",
        role: "assistant",
        content: "（高档 · gpt-5.6-sol）已收到",
        attachments: [],
        tier: "high",
        model: "gpt-5.6-sol",
        status: "completed",
        created_at: "2026-08-30T10:00:02Z"
      }
    ];
    hooks.conversationError = true;

    renderChat();

    expect(screen.getByText(/已收到/)).toBeVisible();
    expect(screen.queryByText(copy.aibrain.loadError)).not.toBeInTheDocument();
  });
});

describe("402 分流：欠费 vs 预留不足（CB 第 1 点）", () => {
  /**
   * 🔴 变异M5：把 chat 里的分流改成 `if (err.status === 402) openRechargeFor("insufficient", …)`
   *    （即 CB 指出的缺陷）→ 本条红。这正是组件级门守不住、必须开在 chat 上的那条。
   */
  it("🔴 OUTSTANDING 402 → 弹欠费说明（需补齐 42.5 / 余额 −42.5），**不出现**「临时预留」话术", async () => {
    hooks.sendMutateAsync.mockRejectedValue(OUTSTANDING);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.outstandingTitle)).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingAmount("42.5"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingBalance("-42.5"))).toBeInTheDocument();
    // 🔴 话术不许串：这笔钱已经花掉了，不存在「结束后退回」。
    expect(dialog().textContent ?? "").not.toContain("临时预留");
    expect(d.queryByText(copy.aibrain.insufficientTitle)).not.toBeInTheDocument();
  });

  /**
   * 🔴 预留不足走另一套，且数字取自 **BE 的 detail**（不是前端下界）。
   * 变异：`openRechargeFor` 不把 `err.detail` 传下去 → 本条红（会退回下界、展示 137.7 但措辞带「至少」，
   *       且 available 会变成钱包的 500 而不是 BE 说的 20）。
   */
  it("🔴 INSUFFICIENT 402 → 预留不足说明，数字取自 BE detail（不是前端下界、不是钱包余额）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(INSUFFICIENT);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientRequired("137.7"))).toBeInTheDocument();
    // 🔴 取 BE 的 20，**不是**钱包里的 500 —— 402 时钱包快照可能已经过时。
    expect(d.getByText(copy.aibrain.insufficientAvailable("20"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientShortfallExact("117.7"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientReserveNote)).toBeInTheDocument();
    // 精确态不带「至少」。
    expect(dialog().textContent ?? "").not.toContain("至少");
    expect(d.queryByText(copy.aibrain.outstandingTitle)).not.toBeInTheDocument();
  });

  /**
   * 🔴🔴 FIX2 · **兜底方向翻转**（本条上一版断言的是「未知 402 → 弹充值窗、按预留不足展示」）。
   *
   * 上一轮那个判断在**只有两个码**时成立（两个都是「充值有效」，把没欠费的人说成欠费更糟）。
   * `2a98b5d0` 加了 `AIBRAIN_INFLIGHT_EXPOSURE_LIMIT` —— 一个「**充值无效**」的 402 之后就不成立了：
   * 猜错方向的代价**不对称** —— 引导充值猜错 = 用户真花了钱还是发不出去（不可逆）；
   * 中性猜错 = 他多点一次顶部那个一直都在的充值入口。
   * 变异：把兜底改回 `openRechargeFor("insufficient", …)` → 本条红（会弹出充值窗）。
   */
  it("🔴 未知 402 码 → **中性提示、不弹充值窗**（方向从「引导充值」翻转）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(new ApiError("nope", "AIBRAIN_SOMETHING_NEW", 402));
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.unknownPaymentIssue));
    // 🔴 不弹充值窗 —— 未知码可能是「充值无效」那一类。
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // 也不许出现任何引导充值 / 说欠费的字样。
    const alertText = screen.getByRole("alert").textContent ?? "";
    expect(alertText).not.toContain("充值");
    expect(alertText).not.toContain("余额");
  });
});

// ══ FIX2 · 三个新码的集成级门（每个码都要在这一层有门——M5 已经证明组件级守不住分流）══════════
describe("🔴 在途敞口 402：不是余额问题，充值无效", () => {
  /** BE `2a98b5d0` aibrain.py:1412-1419 的 detail 真实形状（6 个字段）。 */
  const EXPOSURE = new ApiError(
    "Too much AIBRAIN work is already in progress. Wait for an existing request to finish before retrying.",
    "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT",
    402,
    {
      in_flight_exposure_credits: 5300.8256,
      requested_exposure_credits: 5300.8256,
      exposure_limit_credits: 10601.6512,
      excess_credits: 0.0001,
      in_flight_request_count: 2,
      retryable: true
    }
  );

  /**
   * 🔴🔴 **本包最要紧的一条门**（对标上一轮门D2 钉住「欠费文案里不出现临时预留」的做法）。
   * 敞口上限 = 单请求最大敞口 × multiplier，两个都是 config 常量，**钱包余额不在那个式子里**。
   * 用户充再多钱上限一分不涨 —— 所以这个 402 的界面上**绝不许出现充值引导**。
   * 变异：把这个码的分流改成 `openRechargeFor("insufficient", err.detail)` → 本条红（弹窗出现）。
   */
  it("🔴 敞口 402 → **不弹充值窗**，且提示里不出现「充值 / 余额 / 积分不足」任何字样", async () => {
    hooks.sendMutateAsync.mockRejectedValue(EXPOSURE);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 🔴 一个充值弹窗都不许弹。
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const text = screen.getByRole("alert").textContent ?? "";
    // 🔴 「充值」二字只许以「充值不会解决」的形式出现——故断言的是**引导性说法**不存在。
    expect(text).not.toContain("充值即可");
    expect(text).not.toContain("余额不足");
    expect(text).not.toContain("积分不足");
    // 🔴 主动澄清必须在：用户刚被 402 拦下，默认联想就是没钱，不说破他就会去充值。
    expect(text).toContain(copy.aibrain.inflightExposureNote);
  });

  /**
   * 用上 `in_flight_request_count`（任务包 §三.1 明确要求）——用户据此知道要等几条。
   * 变异：`inflightExposureView` 忽略该字段 → 本条红。
   */
  it("用上 in_flight_request_count → 「当前有 2 条对话正在进行中」", async () => {
    hooks.sendMutateAsync.mockRejectedValue(EXPOSURE);
    renderChat();
    await typeAndSend();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.inflightExposureCount(2))
    );
  });

  /**
   * 用上 `retryable`（任务包 §三.1 明确要求）：为真 → 说「稍后重试即可」；为假 → **不说**。
   * 变异：把 `retryable` 判断写死为 true → 后半段红。
   */
  it("用上 retryable：true → 提示可重试；false → 不承诺重试", async () => {
    hooks.sendMutateAsync.mockRejectedValue(EXPOSURE);
    const first = renderChat();
    await typeAndSend();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.inflightExposureRetry)
    );
    first.unmount();

    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("x", "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT", 402, { in_flight_request_count: 2, retryable: false })
    );
    renderChat();
    await typeAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent ?? "").not.toContain(copy.aibrain.inflightExposureRetry);
  });

  /**
   * detail 缺失 → 不提条数，但「充值不解决」那句**照样在**（那是这个码的立身之本）。
   * 变异：把 `inflightExposureNote` 挪进「有 detail」的分支 → 本条红。
   */
  it("detail 缺失 → 不提条数，但「充值不会解决」照样说", async () => {
    hooks.sendMutateAsync.mockRejectedValue(new ApiError("x", "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT", 402));
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain(copy.aibrain.inflightExposureNote);
    expect(text).not.toMatch(/当前有 \d+ 条/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  /** in_flight_request_count 为 0（自相矛盾）→ 不说条数，别讲「当前有 0 条正在进行中」。 */
  it("in_flight_request_count 为 0 → 不说条数（避免自相矛盾）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("x", "AIBRAIN_INFLIGHT_EXPOSURE_LIMIT", 402, { in_flight_request_count: 0, retryable: true })
    );
    renderChat();
    await typeAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent ?? "").not.toMatch(/当前有 \d+ 条/);
  });
});

describe("422 提示词超限 / 502 上游用量越界 / 502 缺用量", () => {
  /**
   * 422：用户可自行解决 → 讲清三条可做的事；**不弹充值窗**（这不是钱的问题）。
   * 🔴 且**不展示 detail 里的 token 数**：`prompt_token_upper_bound` 是 BE 按 UTF-8 字节算的
   *    保守上界（比真实 token 大不少），摆给用户既看不懂也据此行动不了。
   * 变异：把 token 数拼进文案 → 本条红。
   */
  it("🔴 PROMPT_LIMIT_EXCEEDED(422) → 讲清怎么办、不弹窗、**不出现 token 数字**", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("AIBRAIN prompt exceeds the local safety limit.", "AIBRAIN_PROMPT_LIMIT_EXCEEDED", 422, {
        prompt_token_upper_bound: 950_000,
        max_prompt_tokens: 922_000
      })
    );
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.promptLimitExceeded));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).not.toContain("950000");
    expect(text).not.toContain("922000");
    expect(text).not.toMatch(/\d{4,}/); // 任何四位以上的裸数字都不该出现
  });

  /**
   * 🔴🔴 FIX3 · **502 定稿**（本条上一版断言的是「中性、不许出现『扣费』二字」）。
   *
   * FIX2 时 CB 尚未判定结算性质，写错任何一边都是拿钱说假话，故保持中性——那是对的做法。
   * `fbe8420d` 之后源码给了确定答案：本码与 `USAGE_MISSING`、`PROVIDER_FAILED` **共用**
   * `_fail_chat_message` → `entry_type="release"` 释放全额预留 + `UsageRecord(credits=0)`，
   * **零扣费是代码写死的事实**。所以现在必须**明确告诉用户未扣费**——用户此刻最想知道的就是这个。
   * ⚠️ 码也变了：`..._USAGE_LIMIT_EXCEEDED` 已被 BE 删除（「合法但超上限」改成封顶扣费 + 正常交付，
   *    不再是错误路径），替换为 `..._USAGE_INVALID` 且**不带 detail**。
   * 变异：把文案改回中性（去掉「未扣费」）→ 本条红。
   */
  it("🔴 PROVIDER_USAGE_INVALID(502) → 未扣费 + 等待指引，不诱导立即撞下一次 503", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("AIBRAIN provider returned invalid billing usage.", "AIBRAIN_PROVIDER_USAGE_INVALID", 502)
    );
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.providerUsageInvalid));
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain("未扣费");
    expect(text).toContain("稍等片刻");
    expect(text).not.toContain("请重试");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  /**
   * 🔴 FIX3：`AIBRAIN_PROVIDER_FAILED` 走的是同一个 `_fail_chat_message` → 同样零扣费。
   * 任务包 §一 把「PROVIDER_FAILED 扣不扣费」列为待判定的三条追问之一，源码答案是明确的。
   * 变异：把 `providerFailed` 改回不含「未扣费」→ 本条红。
   */
  it("🔴 PROVIDER_FAILED(502) → 同样明确告知未扣费（与另外两个 502 共用同一释放路径）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(new ApiError("boom", "AIBRAIN_PROVIDER_FAILED", 502));
    renderChat();
    await typeAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.providerFailed));
    expect(screen.getByRole("alert").textContent ?? "").toContain("未扣费");
  });

  /**
   * 🔴🔴 PRICING-UI-0002 §四 · **判定是「改」**（本条上一版断言它走 `providerFailed`）。
   * 我在 FIX4 回执里标注了这条待判：`USAGE_MISSING` **也在** BE 的 `_USER_COOLDOWN_ERROR_CODES` 里
   * → 一定会开冷却 → 说「请重试」等于引导用户立刻去撞 503，连着两次失败。
   * 判定下来与 `REPLAY_GUARD` 同理，故沿用同一句「稍等片刻再发送」。
   * 变异：把它挪回 `PROVIDER_FAILED` 那支 → 本条红。
   */
  it("🔴 USAGE_MISSING(502) → 与 REPLAY_GUARD 同款「稍等片刻」，**不说「请重试」**（它会开冷却）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("AIBRAIN provider returned no billing usage.", "AIBRAIN_USAGE_MISSING", 502)
    );
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.providerReplayGuard));
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain("稍等片刻");
    expect(text).not.toContain("请重试");
    expect(text).not.toContain("no billing usage"); // 不落英文原文
  });

  /**
   * 🔴🔴 §四 点名要的**冷却分组不串味**门：按「会不会开冷却」分成两组，组间文案必须不同。
   *   会开冷却（不许说「请重试」）：`USAGE_INVALID` · `REPLAY_GUARD` · `USAGE_MISSING`
   *   不开冷却（唯一可以说「请稍后重试」）：`PROVIDER_FAILED`
   * 变异：把任一个会开冷却的码挪回 `PROVIDER_FAILED` 那支 → 本条红。
   * 这条比逐个断言更强：它钉住的是**分组关系**，加第七个码时只要归错组就会红。
   */
  it("🔴 三种冷却 502 都要等，只有 PROVIDER_FAILED 可立即重试", async () => {
    const texts: Record<string, string> = {};
    for (const code of [
      "AIBRAIN_PROVIDER_USAGE_INVALID",
      "AIBRAIN_PROVIDER_REPLAY_GUARD",
      "AIBRAIN_USAGE_MISSING",
      "AIBRAIN_PROVIDER_FAILED"
    ]) {
      const view = renderChat();
      hooks.sendMutateAsync.mockRejectedValue(new ApiError("x", code, 502));
      await typeAndSend();
      await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
      texts[code] = screen.getByRole("alert").textContent ?? "";
      view.unmount();
    }

    // 三个会开冷却的：都说「稍等片刻」、都不说「请重试」。
    for (const code of [
      "AIBRAIN_PROVIDER_USAGE_INVALID",
      "AIBRAIN_PROVIDER_REPLAY_GUARD",
      "AIBRAIN_USAGE_MISSING"
    ]) {
      expect(texts[code]).toContain("稍等片刻");
      expect(texts[code]).not.toContain("请重试");
    }
    // 唯一不开冷却的：可以、也应该说「请稍后重试」。
    expect(texts.AIBRAIN_PROVIDER_FAILED).toContain("请稍后重试");
    expect(texts.AIBRAIN_PROVIDER_FAILED).not.toContain("稍等片刻");
    // 🔴 组间文案确实不同（不是三句一模一样蒙混过关）。
    expect(texts.AIBRAIN_PROVIDER_REPLAY_GUARD).not.toBe(texts.AIBRAIN_PROVIDER_FAILED);
    // 三个 502 都要说清零扣费——这一点是共同的，不因分组而丢。
    for (const code of Object.keys(texts)) expect(texts[code]).toContain("未扣费");
  });
});

// ══ FIX3 · 第五个码：503 用量异常冷却 ═══════════════════════════════════════════════════════
// 🔴 这是**冷却**——既不是余额问题（402 那两个），也不是并发太多（敞口那个）。三者的处置完全不同：
//    充值 / 等前面答完 / 等冷却过去。文案串味就等于给了错的指引。
describe("🔴 503 用量异常冷却（第五个码）", () => {
  const COOLDOWN = new ApiError(
    "AIBRAIN provider billing usage is temporarily unavailable. Try again later.",
    "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN",
    503
  );

  /**
   * 🔴🔴 FIX4 · **接真值**（本条上一版断言的是硬编码的「约一分钟」）。
   * `b91e2188` 给 503 补上了 `detail.retry_after_seconds` —— 这正是我上一轮埋在 mock 契约门里
   * 那句「CA 一旦补上字段该门就红，那正是去接真值的时刻」等来的东西。
   * 变异：`cooldownText` 忽略 detail（回到只用硬编码那版）→ 本条红（会说「约一分钟」而不是「85 秒」）。
   */
  it("🔴 503 → 用 detail 里的**真实秒数**（不再是硬编码的「约一分钟」）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("cooldown", "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN", 503, { retry_after_seconds: 85 })
    );
    renderChat();
    await typeAndSend();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.usageAnomalyCooldownRetryIn(85))
    );
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain("85 秒");
    expect(text).not.toContain("约一分钟"); // 有真值就不许再说那个含糊的默认值
    expect(text).not.toContain("temporarily unavailable"); // 不落英文
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  /**
   * 🔴 **回退门**（FIX4 §二.2 明确要求）：`detail` 缺失/字段非法时仍说「约一分钟」。
   * 没有这道门，BE 哪天不发这个字段，UI 会**静默**退化成不说等多久 —— 而"等多久"正是这条提示
   * 唯一有用的信息。理由与 402 那次的 `exact` 分叉一模一样。
   * 变异：删掉 `cooldownText` 的回退分支（直接用 detail 的值）→ 本条红。
   */
  it("🔴 503 回退：detail 缺失/非法 → 仍说「约一分钟」，不会退化成不说等多久", async () => {
    for (const detail of [undefined, null, "nonsense", { retry_after_seconds: 0 }, { retry_after_seconds: "85" }]) {
      const view = renderChat();
      hooks.sendMutateAsync.mockRejectedValue(
        new ApiError("cooldown", "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN", 503, detail)
      );
      await typeAndSend();
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.usageAnomalyCooldown));
      expect(screen.getByRole("alert").textContent ?? "").toContain("约一分钟");
      view.unmount();
    }
  });

  /**
   * 🔴 **三个"被拦下"的码互不串味**（对标门D2 钉住「欠费不提临时预留」的做法）：
   * 冷却 ≠ 余额不足 ≠ 敞口打满。变异：把冷却文案换成任意另外两条 → 本条红。
   */
  it("🔴 冷却文案不与另外四个码串味：无「充值 / 余额 / 同时进行的对话太多 / 未扣费」", async () => {
    hooks.sendMutateAsync.mockRejectedValue(COOLDOWN);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).not.toContain("充值");
    expect(text).not.toContain("余额");
    expect(text).not.toContain(copy.aibrain.inflightExposureNote);
    // 🔴 冷却本身**不涉及这一次的扣费**（这次压根没发出去）——不该顺口说「未扣费」，
    //    那是 502 那条路径（已经调用了上游、需要澄清）才需要的话。
    expect(text).not.toContain("未扣费");
  });

  /**
   * ⚠️ **「租户级」故意不说**（任务包 §二.2 让我判断）。冷却可由同租户任何人触发，但用户
   * 既无法知道是谁、也无法据此做任何事——他唯一能做的就是等。说了只会引出「凭什么因为别人」
   * 的困惑。真正需要知道这件事的是管理员，那属于后台可观测性。
   * 本条把这个决定钉住：文案里不出现「团队 / 其他人 / 租户」这类归因说法。
   */
  it("文案不做「租户级」归因（用户无法理解也无法行动，说了只添困惑）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(COOLDOWN);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).not.toContain("团队");
    expect(text).not.toContain("其他人");
    expect(text).not.toContain("租户");
  });
});

// ══ PRICING-UI-0003 · 冷却**预告**（200 成功响应带 cooldown_retry_after_seconds）═══════════════
// 🔴 它与 503 是同一件事的两个时刻：**预告**（回答已拿到，提醒下一条要等）vs **已撞上**（这次没发出去）。
//    CB 在 PR-REVIEW-RV4 判定「成功后冷却有必要 —— 4097 completion 不是正常回答，因为请求体真实
//    发送了 max_completion_tokens=4096」，所以这条路径合法、必要、罕见，UI 可以做。
describe("🔴 冷却预告（成功响应）", () => {
  /**
   * 变异：把 `handleSend` 里读 `cooldown_retry_after_seconds` 那段删掉 → 本条红。
   * 🔴 断言它是**提示态而不是错误态**：用户刚拿到一个正常回答，渲染成红色 alert 会让他
   *    以为回答有问题。故断言 `role="status"` 且 **`role="alert"` 不存在**。
   */
  it("🔴 字段非空 → 显示预告（含真实秒数），且是**提示态**不是错误态", async () => {
    hooks.sendMutateAsync.mockResolvedValue({ cooldown_retry_after_seconds: 60 });
    renderChat();
    await typeAndSend();

    // ⚠️ 用**文本**定位而不是 getByRole("status")：壳里本来就有别的 status
    //    （MessageStream 的空态、wallet-balance 的低余额徽标），按 role 取会多匹配。
    const ahead = await screen.findByText(copy.aibrain.cooldownAhead(60));
    const text = ahead.textContent ?? "";
    expect(text).toContain("60 秒"); // 真值，不是"约一分钟"
    // 🔴 提示态：它自己是 status（礼貌播报），且**全页没有 alert**、不弹窗。
    expect(ahead.closest('[role="status"]')).not.toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // 🔴 措辞要能看出「回答已拿到」，不许用 503 那套「暂停/出错」的词——那会让用户以为刚才的回答有问题。
    expect(text).toContain("已完成");
    expect(text).not.toContain("异常");
    expect(text).not.toContain("暂停");
  });

  /**
   * 变异：只把秒数存进 state、没有到期 timer → 推进到服务端时长后本条红（旧提示仍永久挂着）。
   * 不做逐秒倒计时；这里只守「到期自动撤掉已经过时的承诺」。
   */
  it("🔴 预告在服务端时长内可见，到期后自动消失", async () => {
    vi.useFakeTimers();
    try {
      hooks.sendMutateAsync.mockResolvedValue({ cooldown_retry_after_seconds: 2 });
      renderChat();
      await typeAndSend();

      expect(screen.getByText(copy.aibrain.cooldownAhead(2))).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_999);
      });
      expect(screen.getByText(copy.aibrain.cooldownAhead(2))).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(screen.queryByText(copy.aibrain.cooldownAhead(2))).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 变异：新响应沿用旧 timer，旧 timer 到点时无条件清 state → 第二条 90 秒预告在第 30 秒被误清。
   * 最后再推进到新值自己的截止点，证明它不是被永久保留。
   */
  it("🔴 新预告替换旧预告后，旧 timer 不得误清新值", async () => {
    vi.useFakeTimers();
    try {
      hooks.sendMutateAsync
        .mockResolvedValueOnce({ cooldown_retry_after_seconds: 60 })
        .mockResolvedValueOnce({ cooldown_retry_after_seconds: 90 });
      renderChat();
      await typeAndSend("第一条");
      expect(screen.getByText(copy.aibrain.cooldownAhead(60))).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      await typeAndSend("第二条");
      expect(screen.getByText(copy.aibrain.cooldownAhead(90))).toBeInTheDocument();

      // t=60s：第一条的旧 timer 到点；第二条仍应再保留 60s。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(screen.getByText(copy.aibrain.cooldownAhead(90))).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(screen.queryByText(copy.aibrain.cooldownAhead(90))).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * 🔴🔴 任务包 §一.4 点名的那条：**字段为 null 时什么都不显示**。
   * 没有这条门，实现退化成「永远显示」不会被发现——而绝大多数请求都是 null，
   * 那就等于给每一次正常对话都挂一句莫名其妙的冷却提示。
   * 变异：把判断写成 `if (ahead !== undefined)` 或干脆无条件 setCooldownAhead → 本条红。
   */
  it("🔴 字段为 null / 缺席 / 0 → **什么都不显示**（绝大多数情况）", async () => {
    for (const payload of [
      { cooldown_retry_after_seconds: null },
      {}, // 字段缺席
      { cooldown_retry_after_seconds: 0 } // 契约坏了（BE 声明 ge=1）→ 宁可不说
    ]) {
      const view = renderChat();
      // 🔴 **每轮先清调用记录**（PRICING-UI-0003 复查补）：`vi.clearAllMocks()` 只在 beforeEach，
      //    不在循环体内。第一轮之后 `toHaveBeenCalled()` 恒为真，下面那句 waitFor 在第 2、3 轮
      //    **是立即通过的空断言** —— 而恰恰是第 2、3 轮（`{}` 与 `0`）在杀「无条件 setCooldownAhead」
      //    那个变异。当时能绿全靠 RTL 在 act 退出时顺手排空了微任务队列，不是靠这句等待。
      //    **一道门只能守它断言的那件事**：这句 waitFor 声称"等到请求发出"，那就得真的每轮都等。
      hooks.sendMutateAsync.mockClear();
      hooks.sendMutateAsync.mockResolvedValue(payload);
      await typeAndSend();
      // 推进到静止点再断言"没有"——否则只看得见点击当下那一帧（NEGATIVE-ASSERT-SWEEP 的教训）。
      await waitFor(() => expect(hooks.sendMutateAsync).toHaveBeenCalledTimes(1));
      // 同上：按文本断言"没有"，避免被壳里其他 status 元素干扰成假红/假绿。
      expect(screen.queryByText(/秒后才能发下一条/)).not.toBeInTheDocument();
      view.unmount();
    }
  });

  /**
   * 🔴 预告与 503 的**先后关系**：两者措辞必须能看出是同一件事的两个时刻，而不是两次故障。
   * 变异：把预告文案换成 503 那句 → 本条红。
   */
  it("🔴 预告与 503 措辞不同：预告说「已完成」，503 说「暂停/异常」", async () => {
    const first = renderChat();
    hooks.sendMutateAsync.mockResolvedValue({ cooldown_retry_after_seconds: 60 });
    await typeAndSend();
    const aheadText = (await screen.findByText(/秒后才能发下一条/)).textContent ?? "";
    first.unmount();

    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("cooldown", "AIBRAIN_PROVIDER_USAGE_ANOMALY_COOLDOWN", 503, { retry_after_seconds: 42 })
    );
    renderChat();
    await typeAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const hitText = screen.getByRole("alert").textContent ?? "";

    expect(aheadText).not.toBe(hitText);
    expect(aheadText).toContain("已完成"); // 预告：回答拿到了
    expect(hitText).toContain("暂停"); // 已撞上：这次没发出去
  });
});

// ══ FIX4 · 第六个码：502 REPLAY_GUARD ═══════════════════════════════════════════════════════
// 🔴 它与 `PROVIDER_FAILED` 对用户**看起来是同一件事**（都没生成出来、都零扣费），但**后果不同**：
//    它在 BE 的 `_USER_COOLDOWN_ERROR_CODES` 里 → **一定会开用户冷却**。说「请重试」等于明知
//    会失败还引导用户去做——他立刻重试必撞 503，体验是连着两次失败。
describe("🔴 502 REPLAY_GUARD（第六个码）：与 PROVIDER_FAILED 不串味", () => {
  const REPLAY = new ApiError(
    "AIBRAIN provider request may have incurred cost.",
    "AIBRAIN_PROVIDER_REPLAY_GUARD",
    502
  );

  /** 变异：把它从分流里删掉（落到 `err.message`）→ 本条红（显示英文原文）。 */
  it("🔴 REPLAY_GUARD → 「未扣费」+「稍等片刻再发送」，**不说「请重试」**", async () => {
    hooks.sendMutateAsync.mockRejectedValue(REPLAY);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.providerReplayGuard));
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain("未扣费"); // 零扣费同样要说
    expect(text).toContain("稍等片刻"); // 🔴 引导等待，而不是立刻重试
    expect(text).not.toContain("请重试"); // 🔴 说了他就会去撞 503
    expect(text).not.toContain("may have incurred cost"); // 不落英文
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  /**
   * 🔴🔴 **本组最要紧的一条**：两个 502 的文案必须**不同**。
   * 变异：把 REPLAY_GUARD 并进 `PROVIDER_FAILED` 那条 `||` → 本条红（两者文案变成同一句）。
   */
  it("🔴 REPLAY_GUARD 与 PROVIDER_FAILED 文案不同（后者可以立刻重试，前者不能）", async () => {
    const first = renderChat();
    hooks.sendMutateAsync.mockRejectedValue(REPLAY);
    await typeAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const replayText = screen.getByRole("alert").textContent ?? "";
    first.unmount();

    hooks.sendMutateAsync.mockRejectedValue(new ApiError("boom", "AIBRAIN_PROVIDER_FAILED", 502));
    renderChat();
    await typeAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const failedText = screen.getByRole("alert").textContent ?? "";

    expect(replayText).not.toBe(failedText);
    // PROVIDER_FAILED 不开冷却 → 它**可以**说「请稍后重试」；REPLAY_GUARD 不行。
    expect(failedText).toContain("请稍后重试");
    expect(replayText).not.toContain("请稍后重试");
  });
});

describe("预检拦截：不发请求，但也要说对是哪一种", () => {
  /**
   * 🔴 余额为负 = 欠费 → 预检就该拦下并说欠费（BE reserve 分支同样把 `available < 0` 判在最前）。
   * 变异：`precheckSend` 去掉负余额那条（回到只有 `<= 0` 的单一 insufficient）→ 本条红。
   */
  it("🔴 余额为负 → 预检拦下（零请求）+ 弹**欠费**说明，欠款额由负余额反推", async () => {
    hooks.wallet = { available_credits: -30 };
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(hooks.sendMutateAsync).not.toHaveBeenCalled(); // 压根没发
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.outstandingTitle)).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.outstandingAmount("30"))).toBeInTheDocument();
  });

  it("余额为 0 → 预检拦下（零请求）+ 弹**预留不足**说明（回退下界 + 「至少」）", async () => {
    hooks.wallet = { available_credits: 0 };
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(hooks.sendMutateAsync).not.toHaveBeenCalled();
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientTitle)).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientMinRequired("68.9"))).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.outstandingTitle)).not.toBeInTheDocument();
  });

  /** 顶部「充值」是用户主动来充 → 两种说明块都不许出现（凭空给数字只会吓人）。 */
  it("顶部「充值」按钮 → 不出现任何缺口/欠费说明", async () => {
    hooks.wallet = { available_credits: -30 }; // 即使正欠着，主动点充值也不该甩说明块
    renderChat();
    fireEvent.click(screen.getByRole("button", { name: copy.aibrain.recharge }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    const d = within(dialog());
    expect(d.queryByText(copy.aibrain.outstandingTitle)).not.toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.insufficientTitle)).not.toBeInTheDocument();
  });
});
