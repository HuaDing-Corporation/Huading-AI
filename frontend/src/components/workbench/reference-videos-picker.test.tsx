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

  it("合计不足 1.8s（单条 1.0s）→ low 提示（可再补，不阻断上传）", async () => {
    inspectByName = { "a.mp4": passing(1.0) };
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
