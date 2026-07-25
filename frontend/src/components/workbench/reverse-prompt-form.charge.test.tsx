import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ── REVERSE-CHARGE-GATE-UI-0001 范围1 · **图片反推计费门**承重 ────────────────────────────────
// 用户验收：图片反推此前**没有任何确认，点「开始反推」直接扣费**（¥0.30/次），而视频路早有计费门。
// 本文件钉住图片路的三条红线（视频路的同款承重在 reverse-prompt-form.video.test.tsx，两边互不塌缩）：
//  门1 金额 `toBe` estimate 返回值（**不许硬编码 30**——分档/费率是租户可覆写的 CreditRate）
//  门2 换素材作废旧报价（报价的 source_asset_id 与当前素材比对；#220 抓到的 P1 同款）
//  门3 estimate 失败 → 弹窗里**一个数字都没有** + 确认禁用 + 取消仍可用（不猜数兜底）

const hooks = vi.hoisted(() => ({
  estimateMutate: vi.fn(),
  estimateReset: vi.fn(),
  estimateState: {
    data: undefined as { credits: number } | undefined,
    variables: undefined as { source_asset_id: string } | undefined,
    isPending: false,
    isError: false
  },
  reverseMutateAsync: vi.fn(),
  uploadMutateAsync: vi.fn()
}));

vi.mock("@/lib/api/hooks", () => ({
  useUploadImage: () => ({ mutateAsync: hooks.uploadMutateAsync, isPending: false }),
  useUploadReverseVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReverseFromAsset: () => ({ mutateAsync: hooks.reverseMutateAsync, isPending: false }),
  useRegenerateReversePrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSaveReversePrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEstimateReversePrompt: () => ({
    mutate: hooks.estimateMutate,
    reset: hooks.estimateReset,
    ...hooks.estimateState
  })
}));

import { copy } from "@/lib/copy";
import { ReversePromptForm } from "./reverse-prompt-form";

/** 上传第 n 张图 → asset id `aid-n`；estimate 状态由各用例摆。 */
function selectImage(name = "a.png") {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], name, { type: "image/png" })] } });
}
const analyzeBtn = () => screen.getByRole("button", { name: copy.reverse.analyze });
const dialog = () => screen.getByRole("dialog");

let uploadSeq = 0;
beforeEach(() => {
  vi.clearAllMocks();
  uploadSeq = 0;
  hooks.estimateState.data = undefined;
  hooks.estimateState.variables = undefined;
  hooks.estimateState.isPending = false;
  hooks.estimateState.isError = false;
  hooks.uploadMutateAsync.mockImplementation(() => Promise.resolve({ asset_id: `aid-${++uploadSeq}` }));
  hooks.reverseMutateAsync.mockResolvedValue({ id: "rp-1", status: "succeeded", result: null, error_code: null });
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});

describe("图片反推计费门（范围1）", () => {
  // 🔴 门1：点「开始反推」不再直接扣费 —— 先开计费门、先取报价；金额 toBe estimate 返回值。
  // 变异：把 imageChargeMessage 的金额硬编码成 30（或任何常量）→ 本条红（桩返 47）。
  it("门1：点开始反推 → 只开门取价、不发起反推；弹窗金额=estimate 返回值(47)，确认后才提交", async () => {
    const view = render(<ReversePromptForm />);
    selectImage();
    await waitFor(() => expect(analyzeBtn()).toBeEnabled());

    fireEvent.click(analyzeBtn());
    // 🔴 NEGATIVE-ASSERT-SWEEP-UI-0001：断言前**推进到静止点**。
    //    裸同步断言只能看见「点击当下这一帧」——「点了先发请求、走一个 await 之后才开门」这种实现
    //    （`onClick={async () => { … }}`，真实世界里最常见的写法）会整个溜过去。
    //    实测：把 `void reverse.mutateAsync(...)` 放进 `Promise.resolve().then(...)` 再开门 → 本条**照样绿**；
    //    加上这行 act 之后同一变异**必红**（回执有前后对照）。
    await act(async () => {});
    // 开门 = 取价，且**一次反推都没发起**（此前的缺陷正是这里直接扣钱）。
    expect(hooks.estimateMutate).toHaveBeenCalledWith({ source_asset_id: "aid-1" });
    expect(hooks.reverseMutateAsync).not.toHaveBeenCalled();
    expect(hooks.estimateReset).toHaveBeenCalled(); // 每次开门都先作废上一次报价

    // 报价回来（47 积分，故意不是 30——硬编码 30 会在此处红）。rerender 让组件读到新的 hook 桩值。
    hooks.estimateState.data = { credits: 47 };
    hooks.estimateState.variables = { source_asset_id: "aid-1" };
    view.rerender(<ReversePromptForm />);

    // 🔴 断言**字面量 47**（而不是 copy.imageChargeMessage(47)）——后者会跟着实现一起漂：把 copy 里的
    // ${credits} 硬编码成 30 时，期望值也变成 30，测试自我抵消、变异抓不到。金额必须来自 estimate 返回值。
    await waitFor(() => expect(within(dialog()).getByText(/47/)).toBeInTheDocument());
    expect(dialog().textContent ?? "").not.toMatch(/\b30\b/); // 任何硬编码档位价都不许出现

    fireEvent.click(within(dialog()).getByRole("button", { name: copy.reverse.chargeConfirm }));
    await waitFor(() => expect(hooks.reverseMutateAsync).toHaveBeenCalledWith({ source_asset_id: "aid-1" }));
  });

  // 🔴 门2：换素材作废旧报价 —— 报价的 source_asset_id 与当前素材对不上时，**不显示任何金额**、确认禁用。
  // 变异：去掉 quote 的资产比对（直接用 estimate.data）→ 本条红（会显示上一张的 47）。
  it("门2：图片A报价47 → 换图片B → 弹窗不显示 A 的金额、确认禁用（旧报价作废）", async () => {
    render(<ReversePromptForm />);
    selectImage("A.png"); // → aid-1
    await waitFor(() => expect(analyzeBtn()).toBeEnabled());

    // A 的报价已在缓存里。
    hooks.estimateState.data = { credits: 47 };
    hooks.estimateState.variables = { source_asset_id: "aid-1" };

    // 换成 B（→ aid-2）：先清除已选图（组件在已选态下不渲染 file input），再选新图。
    // 此时 estimate.data 仍是 A 的 47（TanStack mutation 重跑不清 data，正是 #220 的坑）。
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.reupload }));
    selectImage("B.png");
    await waitFor(() => expect(hooks.uploadMutateAsync).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(analyzeBtn()).toBeEnabled());
    fireEvent.click(analyzeBtn());

    const d = dialog();
    expect(within(d).queryByText(/47/)).not.toBeInTheDocument(); // A 的金额绝不许出现在 B 的弹窗里
    expect(within(d).queryByText(copy.reverse.imageChargeMessage(47))).not.toBeInTheDocument();
    expect(within(d).getByRole("button", { name: copy.reverse.chargeConfirm })).toBeDisabled();
  });

  // 🔴 门3：estimate 失败 → **整个弹窗一个数字都没有**（不猜数兜底）、确认禁用、取消仍可用。
  // 变异：失败时兜底显示任意金额 → 本条红。
  it("门3：estimate 失败 → 弹窗无任何数字 + 确认禁用 + 取消可用（不猜价）", async () => {
    hooks.estimateState.isError = true;
    render(<ReversePromptForm />);
    selectImage();
    await waitFor(() => expect(analyzeBtn()).toBeEnabled());
    fireEvent.click(analyzeBtn());

    const d = dialog();
    expect(within(d).getByText(copy.reverse.chargeEstimateBlocked)).toBeInTheDocument();
    expect(within(d).getByText(copy.errors.reverseEstimateFailed)).toBeInTheDocument();
    // 🔴 一个数字都不出现（含任何阿拉伯数字）。
    expect(d.textContent ?? "").not.toMatch(/\d/);
    expect(within(d).getByRole("button", { name: copy.reverse.chargeConfirm })).toBeDisabled();

    // 取消仍可用 → 关门，且全程零反推请求。
    fireEvent.click(within(d).getByRole("button", { name: copy.common.cancel }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(hooks.reverseMutateAsync).not.toHaveBeenCalled();
  });

  it("估算在途 → 只说在取数、无金额、确认禁用", async () => {
    hooks.estimateState.isPending = true;
    render(<ReversePromptForm />);
    selectImage();
    await waitFor(() => expect(analyzeBtn()).toBeEnabled());
    fireEvent.click(analyzeBtn());

    const d = dialog();
    expect(within(d).getByText(copy.reverse.chargeEstimating)).toBeInTheDocument();
    expect(d.textContent ?? "").not.toMatch(/\d/);
    expect(within(d).getByRole("button", { name: copy.reverse.chargeConfirm })).toBeDisabled();
  });

  // 文案纪律：图片档用图片档的话（不是把 videoChargeMessage 硬塞过来）。
  it("文案：图片档标题/正文是图片档专属，不出现视频档措辞", async () => {
    hooks.estimateState.data = { credits: 30 };
    hooks.estimateState.variables = { source_asset_id: "aid-1" };
    render(<ReversePromptForm />);
    selectImage();
    await waitFor(() => expect(analyzeBtn()).toBeEnabled());
    fireEvent.click(analyzeBtn());

    const d = dialog();
    expect(within(d).getByText(copy.reverse.imageChargeTitle)).toBeInTheDocument();
    expect(within(d).getByText(copy.reverse.imageChargeMessage(30))).toBeInTheDocument();
    expect(within(d).queryByText(copy.reverse.videoChargeTitle)).not.toBeInTheDocument();
    expect(within(d).queryByText(copy.reverse.videoChargeMessage(30))).not.toBeInTheDocument();
  });
});
