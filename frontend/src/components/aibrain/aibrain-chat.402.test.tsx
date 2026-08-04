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
   * 未知的 402 码（如 #239 还在加的「租户级聚合在途敞口」闸门，`e2bc2c02` 里尚未实现）→
   * 落到**较温和**的「预留不足」兜底：把没欠费的人说成欠费，比反过来更糟。
   * 变异：把兜底改成 outstanding → 本条红。
   */
  it("未知 402 码 → 落「预留不足」兜底（不把没欠费的人说成欠费）", async () => {
    hooks.sendMutateAsync.mockRejectedValue(new ApiError("nope", "AIBRAIN_SOMETHING_NEW", 402));
    renderChat();
    await typeAndSend();

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    const d = within(dialog());
    expect(d.getByText(copy.aibrain.insufficientTitle)).toBeInTheDocument();
    expect(d.queryByText(copy.aibrain.outstandingTitle)).not.toBeInTheDocument();
    // 无 detail → 回退到下界 + 「至少」措辞（这条同时守住了回退在真实链路上确实生效）。
    expect(d.getByText(copy.aibrain.insufficientMinRequired("68.8"))).toBeInTheDocument(); // mid 档默认
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
