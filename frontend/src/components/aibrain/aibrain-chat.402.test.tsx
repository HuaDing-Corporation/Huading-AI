import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  wallet: { available_credits: 0 } as { available_credits: number } | undefined
}));

vi.mock("@/lib/aibrain/hooks", () => ({
  useWallet: () => ({ data: hooks.wallet }),
  useCreateConversation: () => ({ mutateAsync: vi.fn().mockResolvedValue({ id: "conv-1" }), isPending: false }),
  useSendMessage: () => ({ mutateAsync: hooks.sendMutateAsync, isPending: false }),
  useConversation: () => ({ data: { messages: [] }, isError: false, refetch: vi.fn() }),
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
  fireEvent.change(screen.getByPlaceholderText(/输入问题/), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: copy.aibrain.send }));
}

beforeEach(() => {
  vi.clearAllMocks();
  hooks.wallet = { available_credits: 500 }; // 正余额 → 预检放行，让请求真发出去，由 BE 的码分流
  hooks.sendMutateAsync.mockResolvedValue({});
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
   * 变异：`openRechargeFor` 不把 `err.detail` 传下去 → 本条红（会退回下界 137.6 但措辞带「至少」，
   *       且 available 会变成钱包的 500 而不是 BE 说的 20）。
   */
  it("🔴 INSUFFICIENT 402 → 预留不足说明，数字取自 BE detail（不是前端下界、不是钱包余额）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(INSUFFICIENT);
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientRequired("137.6"))).toBeInTheDocument();
    // 🔴 取 BE 的 20，**不是**钱包里的 500 —— 402 时钱包快照可能已经过时。
    expect(d.getByText(copy.aibrain.insufficientAvailable("20"))).toBeInTheDocument();
    expect(d.getByText(copy.aibrain.insufficientShortfallExact("117.6"))).toBeInTheDocument();
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
   * 🔴 502 用量越界：**文案暂按中性定稿**（任务包 §六.3 明令，CB 尚未判定其结算性质）。
   * 本条钉住的是「**不承诺任何结算事实**」——「未扣费」「已扣费」都不许出现，
   * 因为在 CB 判定之前这两句话都可能是错的，而这是钱的事。
   * 变异：把文案改成「本次未扣费，请重试」→ 本条红。
   */
  it("🔴 PROVIDER_USAGE_LIMIT_EXCEEDED(502) → 中性文案，**不说扣没扣费**（等 CB 判定）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("envelope", "AIBRAIN_PROVIDER_USAGE_LIMIT_EXCEEDED", 502, {
        reported_prompt_tokens: 922_001,
        max_prompt_tokens: 922_000
      })
    );
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.providerUsageLimit));
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).not.toContain("未扣费");
    expect(text).not.toContain("已扣费");
    expect(text).not.toContain("扣费");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  /**
   * 🔴 `AIBRAIN_USAGE_MISSING`(502) 是**任务包没列、我从源码里捡到的第四个新码**
   * （aibrain.py:502-509）。对用户与 PROVIDER_FAILED 是同一件事 → 共用文案。
   * 变异：把它从分流里删掉 → 本条红（会落到 `err.message` 的英文原文）。
   */
  it("🔴 USAGE_MISSING(502)（任务包未列）→ 走 providerFailed 文案，不落英文原文", async () => {
    hooks.sendMutateAsync.mockRejectedValue(
      new ApiError("AIBRAIN provider returned no billing usage.", "AIBRAIN_USAGE_MISSING", 502)
    );
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy.aibrain.providerFailed));
    expect(screen.getByRole("alert").textContent ?? "").not.toContain("no billing usage");
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
    expect(d.getByText(copy.aibrain.insufficientMinRequired("68.8"))).toBeInTheDocument();
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
