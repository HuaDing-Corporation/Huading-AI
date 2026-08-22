import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const rewriteMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const titlesMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const topicsMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const saveMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const estimateMock = vi.hoisted(() => ({
  data: undefined as
    | {
        estimated_credits: number;
        unit: "credits";
        note: string | null;
        breakdown: Array<{ operation: "rewrite" | "titles" | "topics"; estimated_credits: number }>;
      }
    | undefined,
  isError: false,
  isPending: false,
  isFetching: false
}));

vi.mock("@/lib/api/hooks", () => ({
  useEstimateCopy: () => estimateMock,
  useRewriteCopy: () => ({ mutateAsync: rewriteMock.mutateAsync, isPending: false }),
  useGenerateTitles: () => ({ mutateAsync: titlesMock.mutateAsync, isPending: false }),
  useGenerateTopics: () => ({ mutateAsync: topicsMock.mutateAsync, isPending: false }),
  useSaveCopyDraft: () => ({ mutateAsync: saveMock.mutateAsync, isPending: false })
}));

import { CopywritingForm } from "./copywriting-form";
import { copy } from "@/lib/copy";
import { ApiError } from "@/lib/api/client";

const SOURCE = /粘贴你有权使用/;

beforeEach(() => {
  estimateMock.data = undefined;
  estimateMock.isError = false;
  estimateMock.isPending = false;
  estimateMock.isFetching = false;
  rewriteMock.mutateAsync.mockResolvedValue({ results: [{ text: "改写后的文案" }] });
  titlesMock.mutateAsync.mockResolvedValue({ titles: ["标题A", "标题B"] });
  topicsMock.mutateAsync.mockResolvedValue({ topics: ["#话题A", "#话题B"] });
  saveMock.mutateAsync.mockResolvedValue({ id: "draft-1", created_at: "" });
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
    writable: true
  });
});
afterEach(() => vi.clearAllMocks());

function typeSource(text: string) {
  fireEvent.change(screen.getByPlaceholderText(SOURCE), { target: { value: text } });
}

describe("CopywritingForm (文案仿写 + 标题/话题)", () => {
  it("源文案为空时禁用生成并提示", () => {
    render(<CopywritingForm />);
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeDisabled();
    expect(screen.getByText("请先粘贴参考文案")).toBeInTheDocument();
  });

  it("一键生成并发调 rewrite + titles + topics 并渲染结果", async () => {
    render(<CopywritingForm />);
    typeSource("原始参考文案");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    await waitFor(() => {
      expect(rewriteMock.mutateAsync).toHaveBeenCalledTimes(1);
      expect(titlesMock.mutateAsync).toHaveBeenCalledTimes(1);
      expect(topicsMock.mutateAsync).toHaveBeenCalledTimes(1);
    });
    expect(rewriteMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      source_text: "原始参考文案",
      mode: "smart"
    });
    // smart 模式不带 n / instruction
    expect(rewriteMock.mutateAsync.mock.calls[0][0].n).toBeUndefined();
    expect(rewriteMock.mutateAsync.mock.calls[0][0].instruction).toBeUndefined();

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.getByText("标题A")).toBeInTheDocument();
    expect(screen.getByText("#话题A")).toBeInTheDocument();
  });

  it("自定义模式：显示指令框、未填指令禁用、填后启用", () => {
    render(<CopywritingForm />);
    typeSource("原文");
    expect(screen.queryByPlaceholderText(/更口语/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    expect(screen.getByPlaceholderText(/更口语/)).toBeInTheDocument();
    // 指令空 → 仍禁用
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/更口语/), { target: { value: "更口语、更简短" } });
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeEnabled();

    // 切回智能 → 指令框隐藏
    fireEvent.click(screen.getByRole("button", { name: "智能" }));
    expect(screen.queryByPlaceholderText(/更口语/)).not.toBeInTheDocument();
  });

  it("自定义模式生成时带 instruction、不带 n", async () => {
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    fireEvent.change(screen.getByPlaceholderText(/更口语/), { target: { value: "换高端风格" } });
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    await waitFor(() => expect(rewriteMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(rewriteMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      mode: "custom",
      instruction: "换高端风格"
    });
    expect(rewriteMock.mutateAsync.mock.calls[0][0].n).toBeUndefined();
  });

  it("自动多版：渲染候选卡片、默认载入首条、点击换条、请求带 n", async () => {
    rewriteMock.mutateAsync.mockResolvedValue({
      results: [{ text: "候选一" }, { text: "候选二" }, { text: "候选三" }]
    });
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: "自动多版" }));
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    // 默认载入首条进编辑框
    expect(await screen.findByDisplayValue("候选一")).toBeInTheDocument();
    // 候选卡片可见
    expect(screen.getByText("候选二")).toBeInTheDocument();
    // 点击候选二 → 载入编辑框
    fireEvent.click(screen.getByText("候选二"));
    expect(await screen.findByDisplayValue("候选二")).toBeInTheDocument();
    // auto 请求带 n（默认 3）
    expect(rewriteMock.mutateAsync.mock.calls[0][0]).toMatchObject({ mode: "auto", n: 3 });
  });

  it("保存到历史调 saveCopyDraft，带 source/result/mode/titles/topics", async () => {
    render(<CopywritingForm />);
    typeSource("原文XYZ");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /保存到历史/ }));
    await waitFor(() => expect(saveMock.mutateAsync).toHaveBeenCalledTimes(1));
    expect(saveMock.mutateAsync.mock.calls[0][0]).toMatchObject({
      source_text: "原文XYZ",
      result_text: "改写后的文案",
      mode: "smart",
      titles: ["标题A", "标题B"],
      topics: ["#话题A", "#话题B"]
    });
  });

  it("一键串联：用此文案回调带 result_text（口播 + 电商）", async () => {
    const onUse = vi.fn();
    render(<CopywritingForm onUseInVideo={onUse} />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /数字人口播/ }));
    expect(onUse).toHaveBeenCalledWith("avatar_talk", "改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /电商带货/ }));
    expect(onUse).toHaveBeenCalledWith("seedance_i2v", "改写后的文案");
  });

  it("复制按钮写入剪贴板", async () => {
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /复制/ }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("改写后的文案"));
  });

  // 🔴 CLIPBOARD-TRUTH-0001 补网：非安全上下文（navigator.clipboard 缺失）→ 点复制**不谎报**「已复制」。
  // ⚠️ 必须放行一次微任务再断言（同 copyable-block.test.tsx:54）：坏实现的 setCopiedFlash(true) 落在
  // await 之后的微任务里，同步负断言会先跑完 → 好坏版本都绿 = 假测试（这正是本包在修的那种）。
  // 变异门：把 copyToClipboard 退化成 `await navigator.clipboard?.writeText(x); return true` → 本条转红。
  it("🔴 非安全上下文（无 clipboard）→ 点复制不谎报「已复制」", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true, writable: true });
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    await screen.findByDisplayValue("改写后的文案");

    fireEvent.click(screen.getByRole("button", { name: /复制/ }));
    await new Promise((r) => setTimeout(r, 0)); // 放行微任务：坏实现在这里置「已复制」
    expect(screen.queryByText(copy.workbench.copyCopied)).not.toBeInTheDocument();
  });

  // ══ PRICING-UI-0001 §五 · 扣费披露 + 部分失败可见 ═════════════════════════════════════════
  // 🔴 这条用例的**旧版本在给缺陷站岗**：它叫「标题端点失败时降级不渲染标题区」，断言的全部内容
  //    就是"标题区没渲染"——也就是说，它把 CB 指出的那个缺陷（失败被静默隐藏）当成了正确行为来守护。
  //    用户看到的是"标题莫名其妙没出来"，且无从核对这一路的结果与计费。
  //    现在改成：失败必须**说出来**，且主产出与另一路不受影响。
  // 变异：把 onGenerate 里的 setPartFailures(...) 改回 `ti.status === "fulfilled" ? … : []` 的静默降级
  //      → 本条红（找不到失败提示）。
  it("🔴 §五.3 标题端点失败 → 显式告知「标题生成失败（该项未计费）」，文案与话题照出", async () => {
    // 用 BE 真实会发的错误（502 COPY_GEN_FAILED，services/copy.py `_generate_billed_copy` 的兜底）——
    // 而不是裸 Error：裸 Error 走 errorText 的通用兜底，测不出"原因有没有带给用户"。
    // 🔴 FIX6：BE 的 502 会**带 outcome**（`core/exceptions.py:_copy_generation_error_outcome`
    //    按 URL 后缀生成），所以这一路是"服务端明确失败" → 允许说「未计费」。
    //    ⚠️ 少了这个 outcome，措辞会退到不承诺那句 —— 这正是下面 billing 那组门要证的事。
    titlesMock.mutateAsync.mockRejectedValue(
      new ApiError("Copy generation failed.", "COPY_GEN_FAILED", 502, null, { operation: "titles", status: "failed" })
    );
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    // 失败这件事本身必须出现在界面上，并写明该项不计费、以及为什么失败。
    const failure = await screen.findByText(/标题生成失败/);
    expect(failure.textContent ?? "").toContain("未计费");
    expect(failure.textContent ?? "").toContain("Copy generation failed.");
    // 不阻断：主产出与另一路照常。
    expect(screen.queryByText("标题候选（点击复制）")).not.toBeInTheDocument();
    expect(screen.getByText("#话题A")).toBeInTheDocument();
    // 话题这一路成功 → 不许连坐报失败。
    expect(screen.queryByText(/话题生成失败/)).not.toBeInTheDocument();
  });

  /**
   * 🔴 rewrite 也是三个独立计费端点之一，不能只把 titles/topics 接进资金判据。
   * 本门还先产出一版旧结果：第二轮请求一发起，旧 result/candidates/titles/topics 与动作必须一起撤下，
   * 不能等 rewrite 失败后才清，更不能在等待期间继续复制、保存或带入上一轮内容。
   */
  it("🔴 第二轮 in-flight 前撤下全部旧结果；随后 rewrite 502 → 文案改写未计费", async () => {
    rewriteMock.mutateAsync.mockResolvedValueOnce({
      results: [{ text: "旧主结果" }, { text: "旧候选" }]
    });
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: "自动多版" }));
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    expect(await screen.findByDisplayValue("旧主结果")).toBeInTheDocument();
    expect(screen.getByText("旧候选")).toBeInTheDocument();

    let rejectSecond!: (reason: unknown) => void;
    rewriteMock.mutateAsync.mockImplementationOnce(
      () => new Promise((_, reject) => {
        rejectSecond = reject;
      })
    );
    fireEvent.change(screen.getByLabelText(copy.workbench.copySourceLabel), {
      target: { value: "新一轮原文" }
    });
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    // 第二轮请求仍在飞：不能等失败落地后才撤旧内容，否则此窗口里仍可复制/保存/带入旧结果。
    await waitFor(() => expect(screen.queryByDisplayValue("旧主结果")).not.toBeInTheDocument());
    expect(screen.queryByText("旧候选")).not.toBeInTheDocument();
    expect(screen.queryByText("标题A")).not.toBeInTheDocument();
    expect(screen.queryByText("#话题A")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.workbench.copySave })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.workbench.copyUseInAvatar })).not.toBeInTheDocument();

    await act(async () => {
      rejectSecond(new ApiError("Copy generation failed.", "COPY_GEN_FAILED", 502, null, {
        operation: "rewrite",
        status: "failed"
      }));
    });

    const failure = await screen.findByText(/文案改写生成失败/);
    expect(failure.textContent ?? "").toContain("未计费");
    // rewrite 不再同时落一条通用 error；否则同一失败会重复显示两次。
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  /** 无响应只能陈述观测事实，不能把 rewrite 特判成“未计费”。 */
  it("🔴 rewrite 网络中断/无 outcome → 不承诺未计费，指向用量记录", async () => {
    rewriteMock.mutateAsync.mockRejectedValue(
      new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0)
    );
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    const failure = await screen.findByText(/文案改写没有完成/);
    const text = failure.textContent ?? "";
    expect(text).not.toMatch(/未计费|不计费|不扣费/);
    expect(text).toContain("以用量记录为准");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  /**
   * 🔴 §五.4 相关：文案端点余额不足时 BE 发的是 **403 TENANT_QUOTA_EXCEEDED**
   * （每个端点各自 `reserve_copy_quota` → `_apply_active_quota_delta`，即"逐个尝试"而非总额预检）。
   * 这条同时守住本包顺手修的那个 P1：error-text.ts 此前只认小写码，配额不足会漏成英文
   * "Insufficient tenant quota."。变异：把 error-text.ts 改回小写比较 → 本条红。
   * ⚠️ 「余额不足该如何处置」（要不要整体拦、要不要提示充值）任务包要求与 CA 对齐后再定，
   *    本条只钉「不许把英文原文甩给用户」这条底线，不预设处置策略。
   */
  it("🔴 §五.4 话题端点配额不足(403 TENANT_QUOTA_EXCEEDED) → 显示中文额度提示，不是英文原文", async () => {
    topicsMock.mutateAsync.mockRejectedValue(
      new ApiError("Insufficient tenant quota.", "TENANT_QUOTA_EXCEEDED", 403, null, {
        operation: "topics",
        status: "failed"
      })
    );
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    const failure = await screen.findByText(/话题生成失败/);
    expect(failure.textContent ?? "").toContain(copy.errors.quota);
    expect(failure.textContent ?? "").not.toContain("Insufficient tenant quota.");
  });

  /** 两路同时失败 → 两条都要列出来（不是只报第一条）。变异：只取首个失败 → 本条红。 */
  it("🔴 §五.3 标题与话题同时失败 → 两条失败各自列出", async () => {
    // 裸 Error（没有服务端 outcome）→ FIX6 起走**不承诺**那句「没有完成」。
    titlesMock.mutateAsync.mockRejectedValue(new Error("titles boom"));
    topicsMock.mutateAsync.mockRejectedValue(new Error("topics boom"));
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.getByText(/标题没有完成/)).toBeInTheDocument();
    expect(screen.getByText(/话题没有完成/)).toBeInTheDocument();
  });

  // ══ FIX6 · P1-2 · 界面上到底说了哪句话 ════════════════════════════════════════════════════
  // `copy-billing.test.ts` 证的是**判据本身**；这一组证的是**判据接进了 UI**。
  // 🔴 分成两处的理由和 FIX1 的 M5 教训一样：纯函数门绿着，组件里根本没调用它，照样全绿。
  //    （那次是 `pricing-disclosure.test.tsx` 只测弹窗渲染、没测 `aibrain-chat` 的路由。）

  /**
   * 🔴🔴 **任务包点名的那个变异**：「把'无响应'也走成'未计费'」。
   * 变异：`copywriting-form.tsx` 里恒用 `copyPartFailedReleased` → 本条红。
   * 这里断言的是**否定式**：不出现「未计费」「不计费」这类承诺 —— 因为安全的说法有很多种，
   * 而危险的说法只有"承诺了"这一种，钉死后者才不会被换个措辞绕过。
   */
  it("🔴 网络中断（NETWORK_ERROR，无响应）→ 文案绝不出现「未计费 / 不计费」这类资金承诺", async () => {
    titlesMock.mutateAsync.mockRejectedValue(
      new ApiError("网络连接失败，请检查后端服务是否在线。", "NETWORK_ERROR", 0)
    );
    const { container } = render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    const failure = await screen.findByText(/标题没有完成/);
    const text = failure.textContent ?? "";
    expect(text).not.toContain("未计费");
    expect(text).not.toContain("不计费");
    expect(text).not.toContain("不扣费");
    // 说清没完成 + 指向权威来源（用量记录），不替服务端下资金结论。
    expect(text).toContain("以用量记录为准");
    // 整页也不许有这三个词从别处冒出来。
    expect(container.textContent ?? "").not.toMatch(/未计费|不计费|不扣费/);
  });

  /**
   * 🔴 网关 504：**有** HTTP 状态、**没有** outcome —— 服务端可能已经跑完并 settle。
   * 变异：判据换成 `err.status === 0 ? unknown : released`（只防"没状态码"那一种）→ 本条红。
   */
  it("🔴 网关 504（有状态码但无服务端 outcome）→ 同样不承诺", async () => {
    topicsMock.mutateAsync.mockRejectedValue(new ApiError("请求失败（504）", "HTTP_ERROR", 504));
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    const failure = await screen.findByText(/话题没有完成/);
    expect(failure.textContent ?? "").not.toMatch(/未计费|不计费/);
  });

  /**
   * 🔴 对照组（**分流真的分了**，不是一律降级成"不承诺"）：
   * 带服务端 failed outcome 的业务失败 → 仍然据实说「未计费」。
   * 变异：`copyFailureBilling` 恒返回 "unknown"（等于全走路 A，把 B 那一路也砍了）→ 本条红。
   */
  it("🔴 有服务端 outcome 的业务失败 → 据实说「未计费」（分流两侧都要活着）", async () => {
    topicsMock.mutateAsync.mockRejectedValue(
      new ApiError("Copy generation failed.", "COPY_GEN_FAILED", 502, null, { operation: "topics", status: "failed" })
    );
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    const failure = await screen.findByText(/话题生成失败/);
    expect(failure.textContent ?? "").toContain("未计费");
  });

  /**
   * 🔴 500 未处理异常**带着 outcome** —— 唯一"看起来像业务失败、实际可能已扣费"的形态
   * （`_generate_billed_copy` 的 except 覆盖不到 `return` 之后）。
   * 变异：去掉 `copyFailureBilling` 里的 `status === 500` 排除 → 本条红。
   * ⚠️ 与 `copy-billing.test.ts` 那条 500 门断言的是同一规则的**两个层次**：那条证判据，这条证 UI。
   */
  it("🔴 500 + outcome（settle 之后出错也长这样）→ 界面同样不承诺", async () => {
    titlesMock.mutateAsync.mockRejectedValue(
      new ApiError("Internal server error.", "INTERNAL_SERVER_ERROR", 500, null, {
        operation: "titles",
        status: "failed"
      })
    );
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    const failure = await screen.findByText(/标题没有完成/);
    expect(failure.textContent ?? "").not.toMatch(/未计费|不计费/);
  });

  /** 全成 → 一条失败提示都不出现（防止把提示写成常驻）。 */
  it("§五.3 三路全成 → 不出现任何失败提示", async () => {
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.queryByText(/生成失败/)).not.toBeInTheDocument();
  });

  /** 生产变异：不读取 estimate、继续常驻无金额回退 → 本条红，服务端的 24 不会出现。 */
  it("🔴 estimate 成功 → 发起生成前显示服务端 total，并明确是预计金额", () => {
    estimateMock.data = {
      estimated_credits: 24,
      unit: "credits",
      note: "本报价包含文案改写、标题和话题三项。",
      breakdown: [
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "titles", estimated_credits: 8 },
        { operation: "topics", estimated_credits: 8 }
      ]
    };

    render(<CopywritingForm />);

    const disclosure = screen.getByText(/预计本次生成约 24 积分/);
    expect(disclosure).toBeInTheDocument();
    expect(disclosure.textContent ?? "").toContain("改写、标题和话题三项");
    expect(disclosure.textContent ?? "").not.toContain("将扣费 24");
  });

  /**
   * 🔴 P2：报价由 query 异步到达，视觉文本更新之外还必须礼貌播报；`aria-atomic` 保证整句重读，
   * 不让读屏只听见孤立的“24”。变异：删除价格披露上的 aria-live → 本条红。
   */
  it("🔴 estimate 从等待态异步更新为服务端报价 → 同一披露区以 polite live region 整句播报", () => {
    estimateMock.isPending = true;
    const { rerender } = render(<CopywritingForm />);

    const pendingDisclosure = screen.getByText(copy.workbench.copyPriceDisclosure);
    expect(pendingDisclosure).toHaveAttribute("aria-live", "polite");
    expect(pendingDisclosure).toHaveAttribute("aria-atomic", "true");

    estimateMock.isPending = false;
    estimateMock.data = {
      estimated_credits: 24,
      unit: "credits",
      note: "本报价包含文案改写、标题和话题三项。",
      breakdown: [
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "titles", estimated_credits: 8 },
        { operation: "topics", estimated_credits: 8 }
      ]
    };
    rerender(<CopywritingForm />);

    const quotedDisclosure = screen.getByText(/预计本次生成约 24 积分/);
    expect(quotedDisclosure).toHaveAttribute("aria-live", "polite");
    expect(quotedDisclosure).toHaveAttribute("aria-atomic", "true");
  });

  /**
   * 🔴 生产变异：删掉 total/breakdown 相等校验，直接信 `estimated_credits` → 本条红，会露出 99。
   * 期望只断服务端给出的矛盾 total 不出现，不拿 breakdown 的 24 当替代价格——前端不自行报价。
   */
  it("🔴 breakdown 三项之和与 total 不一致 → 隐藏 total，回退到无金额披露", () => {
    estimateMock.data = {
      estimated_credits: 99,
      unit: "credits",
      note: "本报价包含文案改写、标题和话题三项。",
      breakdown: [
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "titles", estimated_credits: 8 },
        { operation: "topics", estimated_credits: 8 }
      ]
    };

    render(<CopywritingForm />);

    expect(screen.getByText(copy.workbench.copyPriceDisclosure)).toBeInTheDocument();
    expect(screen.queryByText(/99\s*积分/)).not.toBeInTheDocument();
    expect(screen.queryByText(/24\s*积分/)).not.toBeInTheDocument();
  });

  /** 生产变异：只验 sum、不验三种 operation 各一次 → 本条红（总和仍是 24，形状却已漂移）。 */
  it("🔴 breakdown 总和虽一致但 operation 重复/缺项 → 仍隐藏 total", () => {
    estimateMock.data = {
      estimated_credits: 24,
      unit: "credits",
      note: "本报价包含文案改写、标题和话题三项。",
      breakdown: [
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "titles", estimated_credits: 8 }
      ]
    };

    render(<CopywritingForm />);

    expect(screen.getByText(copy.workbench.copyPriceDisclosure)).toBeInTheDocument();
    expect(screen.queryByText(/24\s*积分/)).not.toBeInTheDocument();
  });

  /**
   * 🔴 apiFetch 不做运行时 schema 校验；坏响应里的 null item 必须安全降级，不能让整个文案页崩溃。
   * 变异：直接读取 `item.estimated_credits` 而不先验对象 → 本条因 render 抛 TypeError 而红。
   */
  it("🔴 breakdown 含 null 项 → 不崩溃，隐藏 total 并回退无金额披露", () => {
    estimateMock.data = {
      estimated_credits: 24,
      unit: "credits",
      note: null,
      breakdown: [
        null,
        { operation: "titles", estimated_credits: 8 },
        { operation: "topics", estimated_credits: 8 }
      ]
    } as unknown as typeof estimateMock.data;

    render(<CopywritingForm />);

    expect(screen.getByText(copy.workbench.copyPriceDisclosure)).toBeInTheDocument();
    expect(screen.queryByText(/24\s*积分/)).not.toBeInTheDocument();
  });

  /**
   * 🔴 生产变异：请求失败时仍信 React Query 留下的旧 data → 本条红，会继续展示过期的 24。
   * 用“error + stale data”而不是 data=undefined，确保本门真正在守失败分支，不是被空值顺手防住。
   */
  it("🔴 estimate 请求失败 → 即使缓存残留旧报价也回退，且不出现任何猜测数字", () => {
    estimateMock.data = {
      estimated_credits: 24,
      unit: "credits",
      note: "本报价包含文案改写、标题和话题三项。",
      breakdown: [
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "titles", estimated_credits: 8 },
        { operation: "topics", estimated_credits: 8 }
      ]
    };
    estimateMock.isError = true;

    render(<CopywritingForm />);

    const fallback = screen.getByText(copy.workbench.copyPriceDisclosure);
    expect(fallback).toBeInTheDocument();
    expect(fallback.textContent ?? "").not.toMatch(/\d+\s*积分/);
    expect(screen.queryByText(/24\s*积分/)).not.toBeInTheDocument();
  });

  /**
   * 🔴 reconnect / 显式 invalidation 或 refetch 等触发后台刷新时，React Query 会保留旧 data；
   * 新响应尚未落地前不能把旧价冒充当前报价。全局 refetchOnWindowFocus=false，切回窗口不会刷新；
   * 长期持续挂载页面又无轮询，可无限保留旧报价，仍是明确债项。本门只守“已经开始刷新时隐藏旧值”。
   */
  it("🔴 estimate 正在 refetch → 即使缓存有旧报价也先回退，不展示过期金额", () => {
    estimateMock.data = {
      estimated_credits: 24,
      unit: "credits",
      note: null,
      breakdown: [
        { operation: "rewrite", estimated_credits: 8 },
        { operation: "titles", estimated_credits: 8 },
        { operation: "topics", estimated_credits: 8 }
      ]
    };
    estimateMock.isFetching = true;

    render(<CopywritingForm />);

    expect(screen.getByText(copy.workbench.copyPriceDisclosure)).toBeInTheDocument();
    expect(screen.queryByText(/24\s*积分/)).not.toBeInTheDocument();
  });

  /**
   * 生产变异：estimate 未返回时写死一个默认金额 → 本条红。
   * 这与上面的请求失败门分工：本条守“尚无响应”，失败门守“错误态仍残留旧 data”。
   */
  it("🔴 estimate 未返回 → 披露计费结构但不猜任何金额", () => {
    const { container } = render(<CopywritingForm />);
    const disclosure = screen.getByText(copy.workbench.copyPriceDisclosure);
    expect(disclosure).toBeInTheDocument();

    const text = disclosure.textContent ?? "";
    // 结构性事实可核查且与租户费率无关。
    expect(text).toContain("三项");
    // 🔴 FIX6 · P1-2：「未成功的那一项**不计费**」已从披露里**删除** ——
    //    那是前端保证不了的资金事实（响应丢失时服务端可能已 settle）。
    //    失败项的措辞下沉到逐条提示，按 `copyFailureBilling` 分流。
    expect(text).not.toContain("不计费");
    expect(text).toContain("三次计费"); // 结构性事实（可核查）保留
    // 没有服务端报价时，一个硬编码金额都不许有。
    expect(text).not.toMatch(/\d+\s*积分/);
    // 顺带否掉最容易复发的那两个具体值。
    expect(text).not.toContain("3 积分");
    expect(text).not.toContain("1 积分");
    // 「不扣费」的谎不许回来。
    expect(container.textContent ?? "").not.toContain("不扣费");
  });
});
