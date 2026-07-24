import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { ReferenceVideoInspection } from "@/lib/media/reference-video";

const uploadMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/lib/api/hooks", () => ({
  useUploadVideoGenReference: () => ({ mutateAsync: uploadMock.mutateAsync, isPending: false })
}));

import { ReferenceVideosPicker } from "./reference-videos-picker";

// jsdom 无法解码视频 → 注入受控 inspect（对齐 avatar-video-picker 的注入模式）。按文件名派发结果。
const passing = (duration: number, extra?: Partial<ReferenceVideoInspection>): ReferenceVideoInspection => ({
  error: null,
  meta: { duration, width: 1280, height: 720 },
  willTranscode: false,
  willDownscale: false,
  ...extra
});
const rejecting = (error: string): ReferenceVideoInspection => ({ error, meta: null, willTranscode: false, willDownscale: false });

let inspectByName: Record<string, ReferenceVideoInspection>;
const inspect = (file: File) => Promise.resolve(inspectByName[file.name] ?? passing(5));

let vidSeq = 0;
beforeEach(() => {
  vidSeq = 0;
  inspectByName = {};
  URL.createObjectURL = vi.fn(() => `blob:v${++vidSeq}`);
  URL.revokeObjectURL = vi.fn();
  uploadMock.mutateAsync.mockImplementation(() => Promise.resolve({ asset_id: `video-asset-${vidSeq}` }));
});
afterEach(() => vi.clearAllMocks());

const pickFiles = (...names: string[]) => {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map((n) => new File([""], n, { type: "video/mp4" })) } });
};
const uploadBtn = () => screen.getByRole("button", { name: /添加参考视频/ });

describe("ReferenceVideosPicker (V2V · D5/D9/D10)", () => {
  it("D5：「参考视频不能包含真人 + 不扣积分」显著明示（默认即可见，非折叠）", () => {
    render(<ReferenceVideosPicker inspect={inspect} />);
    expect(screen.getByText(copy.workbench.vgRefVideoNoHuman)).toBeInTheDocument();
  });

  it("上传 1 条（5s）→ 计数 1/3 + 合计时长显示 5.0 秒 + <video> 预览", async () => {
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4");
    await waitFor(() => expect(screen.getByRole("button", { name: /（1\/3）/ })).toBeInTheDocument());
    expect(screen.getByText(copy.workbench.vgRefVideoTotal("5.0"))).toBeInTheDocument();
    expect(document.querySelector("video")).not.toBeNull(); // 视频预览分支（非 <img>）
  });

  // 🔴 D10 承重（本包 UX 核心）：合计将超 15.2s 的那条**不上传、当场告知**——不让用户传完才被告知。
  // 变异：去掉 onFiles 的 nextTotal 前置闸 → 本条红（第二条被上传、合计 20 出现）。
  it("D10 联动前置闸：已 12s 再传 8s → 第二条被拒不上传（合计超限提示），已传保持 1 条", async () => {
    inspectByName = { "a.mp4": passing(12), "b.mp4": passing(8) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4");
    await waitFor(() => expect(screen.getByRole("button", { name: /（1\/3）/ })).toBeInTheDocument());
    pickFiles("b.mp4");
    await waitFor(() => expect(screen.getByText(copy.workbench.vgRefVideoTotalOver)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /（1\/3）/ })).toBeInTheDocument(); // 仍 1 条
    expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1); // b.mp4 未发上传请求
  });

  // 🔴 FIX2 承重（CB P1 · 闭包时序）：**同一个 FileList** 里 8s+8s——itemsRef 在 render 前不更新，若每轮重读 ref
  // 两条都按 0+8 过闸双双上传（D10 失效）。局部累计修复后：第二条的闸看到第一条的 8s → 只上传一次。
  // 变异：把闸改回每轮重读 itemsRef.current → 本条红（mutateAsync 被调 2 次）。
  it("FIX2 同批多选 8s+8s → 只上传一次（本批累计闸拦第二条）+ 超限提示", async () => {
    inspectByName = { "a.mp4": passing(8), "b.mp4": passing(8) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4", "b.mp4"); // 同一个 FileList
    await waitFor(() => expect(screen.getByText(copy.workbench.vgRefVideoTotalOver)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /（1\/3）/ })).toBeInTheDocument();
    expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(1); // 第二条在上传前被本批累计闸拦下
  });

  it("FIX2 同批 5s+5s+5s（合计 15 < 15.2 合法）→ 三条全上传", async () => {
    inspectByName = { "a.mp4": passing(5), "b.mp4": passing(5), "c.mp4": passing(5) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4", "b.mp4", "c.mp4");
    await waitFor(() => expect(screen.getByRole("button", { name: /（3\/3）/ })).toBeInTheDocument());
    expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(3);
  });

  // FIX1：合计开区间——单条恰 1.8s 合法（闭区间）但合计恰 1.8 不满足 BE `MIN < total` → low 提示（可再补，不阻断上传）。
  it("合计恰 1.8s（单条 1.8s 合法）→ low 提示（开区间对齐 BE）", async () => {
    inspectByName = { "a.mp4": passing(1.8) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4");
    await waitFor(() => expect(screen.getByText(new RegExp(copy.workbench.vgRefVideoTotalLow))).toBeInTheDocument());
  });

  // D10：3 条上限——第 4 条不上传并明确提示（不静默截断）。
  it("3 条上限：一次选 4 个 → 只收 3 条 + 超数提示；按钮转满额禁用", async () => {
    inspectByName = { "a.mp4": passing(2), "b.mp4": passing(2), "c.mp4": passing(2), "d.mp4": passing(2) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4", "b.mp4", "c.mp4", "d.mp4");
    await waitFor(() => expect(screen.getByRole("button", { name: /（3\/3）/ })).toBeInTheDocument());
    expect(screen.getByText(copy.workbench.vgRefVideoOverCount)).toBeInTheDocument();
    expect(uploadBtn()).toBeDisabled();
    expect(uploadMock.mutateAsync).toHaveBeenCalledTimes(3);
  });

  // D9：单条超长（>15.2s）拒绝 + 提示自行剪辑，不上传、不静默。
  it("单条 20s → 拒绝提示「15 秒以内」，不上传", async () => {
    inspectByName = { "long.mp4": rejecting(copy.workbench.vgRefVideoTooLong) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("long.mp4");
    await waitFor(() => expect(screen.getByText(copy.workbench.vgRefVideoTooLong)).toBeInTheDocument());
    expect(uploadMock.mutateAsync).not.toHaveBeenCalled();
  });

  // D9：MOV→转码告知、>720p→压缩告知（通过上传但徽标不静默）。
  it("转码/降码告知徽标：willTranscode + willDownscale 显示在预览上", async () => {
    inspectByName = { "a.mp4": passing(5, { willTranscode: true, willDownscale: true }) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4");
    await waitFor(() => expect(screen.getByText(new RegExp(copy.workbench.vgRefVideoWillTranscode))).toBeInTheDocument());
    expect(screen.getByText(new RegExp(copy.workbench.vgRefVideoWillDownscale))).toBeInTheDocument();
  });

  // 🔴 BADGE-OVERLAP-FIX 承重（生产实拍：压缩徽标盖住删除按钮 → 有徽标的视频删不掉）：
  // ① 交互语义：**有徽标时**点删除 → 条目真被移除（当初承重没覆盖「有徽标 + 删除」组合，本条补上）。
  // ② 结构钉（jsdom 无 hit-testing，用结构断言对抗层叠回归；变异：告知移回 top / 去 z-10 / 底部条去
  //    pointer-events-none → 本条红）：删除按钮 z-10；告知在 pointer-events-none 的底部条内（非顶部角）。
  it("有压缩/转码徽标时删除按钮仍可点且能删除；徽标在底部条、按钮 z-10（结构互不遮挡）", async () => {
    inspectByName = { "a.mp4": passing(5, { willTranscode: true, willDownscale: true }) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4");
    await waitFor(() => expect(screen.getByRole("button", { name: /（1\/3）/ })).toBeInTheDocument());
    // ② 结构：按钮提层；告知徽标位于 pointer-events-none 底部条（不在顶部角与按钮抢位）。
    const del = screen.getByRole("button", { name: copy.workbench.removeVideo });
    expect(del.className).toContain("z-10");
    const badge = screen.getByText(new RegExp(copy.workbench.vgRefVideoWillDownscale));
    const bottomBar = badge.parentElement as HTMLElement;
    expect(bottomBar.className).toContain("pointer-events-none");
    expect(bottomBar.className).toContain("bottom-0");
    expect(bottomBar.className).not.toContain("top-");
    // ① 交互：点删除 → 条目移除（回到 0/3）、预览 URL 释放。
    fireEvent.click(del);
    expect(screen.getByRole("button", { name: /（0\/3）/ })).toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  // D8：互斥禁用（父级传 disabled）→ 上传按钮禁用 + 原因说明。
  it("disabled（已传参考图）→ 按钮禁用 + 互斥原因可见", () => {
    render(<ReferenceVideosPicker inspect={inspect} disabled disabledHint={copy.workbench.vgRefMediaExclusiveImages} />);
    expect(uploadBtn()).toBeDisabled();
    expect(screen.getByText(copy.workbench.vgRefMediaExclusiveImages)).toBeInTheDocument();
  });

  it("移除一条 → 合计随之更新、object URL 释放", async () => {
    inspectByName = { "a.mp4": passing(6), "b.mp4": passing(4) };
    render(<ReferenceVideosPicker inspect={inspect} />);
    pickFiles("a.mp4", "b.mp4");
    await waitFor(() => expect(screen.getByText(copy.workbench.vgRefVideoTotal("10.0"))).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("button", { name: copy.workbench.removeVideo })[0]);
    expect(screen.getByText(copy.workbench.vgRefVideoTotal("4.0"))).toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
});
