import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const rewriteMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const titlesMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const topicsMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const saveMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("@/lib/api/hooks", () => ({
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
  //    用户看到的是"标题莫名其妙没出来"，且**不知道为什么只扣了 2 分而不是 3 分**。
  //    现在改成：失败必须**说出来**，且主产出与另一路不受影响。
  // 变异：把 onGenerate 里的 setPartFailures(...) 改回 `ti.status === "fulfilled" ? … : []` 的静默降级
  //      → 本条红（找不到失败提示）。
  it("🔴 §五.3 标题端点失败 → 显式告知「标题生成失败（该项未计费）」，文案与话题照出", async () => {
    // 用 BE 真实会发的错误（502 COPY_GEN_FAILED，services/copy.py `_generate_billed_copy` 的兜底）——
    // 而不是裸 Error：裸 Error 走 errorText 的通用兜底，测不出"原因有没有带给用户"。
    titlesMock.mutateAsync.mockRejectedValue(new ApiError("Copy generation failed.", "COPY_GEN_FAILED", 502));
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
   * 🔴 §五.4 相关：文案端点余额不足时 BE 发的是 **403 TENANT_QUOTA_EXCEEDED**
   * （每个端点各自 `reserve_copy_quota` → `_apply_active_quota_delta`，即"逐个尝试"而非总额预检）。
   * 这条同时守住本包顺手修的那个 P1：error-text.ts 此前只认小写码，配额不足会漏成英文
   * "Insufficient tenant quota."。变异：把 error-text.ts 改回小写比较 → 本条红。
   * ⚠️ 「余额不足该如何处置」（要不要整体拦、要不要提示充值）任务包要求与 CA 对齐后再定，
   *    本条只钉「不许把英文原文甩给用户」这条底线，不预设处置策略。
   */
  it("🔴 §五.4 话题端点配额不足(403 TENANT_QUOTA_EXCEEDED) → 显示中文额度提示，不是英文原文", async () => {
    topicsMock.mutateAsync.mockRejectedValue(
      new ApiError("Insufficient tenant quota.", "TENANT_QUOTA_EXCEEDED", 403)
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
    titlesMock.mutateAsync.mockRejectedValue(new Error("titles boom"));
    topicsMock.mutateAsync.mockRejectedValue(new Error("topics boom"));
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.getByText(/标题生成失败/)).toBeInTheDocument();
    expect(screen.getByText(/话题生成失败/)).toBeInTheDocument();
  });

  /** 全成 → 一条失败提示都不出现（防止把提示写成常驻）。 */
  it("§五.3 三路全成 → 不出现任何失败提示", async () => {
    render(<CopywritingForm />);
    typeSource("原文");
    fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.queryByText(/生成失败/)).not.toBeInTheDocument();
  });

  /**
   * 🔴 §五.1/§五.2：扣费披露必须在**发起之前**就在界面上，且说清「一键 3 积分 / 各 1 积分」。
   * 变异：删掉那行 `copy.workbench.copyPriceDisclosure` → 本条红。
   * 🔴 顺带钉住「不扣费」三个字**不许**再出现在这个界面上（那是本包修掉的谎）。
   */
  it("🔴 §五.1/2 界面上有扣费披露（3 积分 / 各 1 积分），且不再出现「不扣费」", () => {
    const { container } = render(<CopywritingForm />);
    const disclosure = screen.getByText(copy.workbench.copyPriceDisclosure);
    expect(disclosure).toBeInTheDocument();
    expect(disclosure.textContent ?? "").toContain("3 积分");
    expect(disclosure.textContent ?? "").toContain("1 积分");
    expect(container.textContent ?? "").not.toContain("不扣费");
  });
});
