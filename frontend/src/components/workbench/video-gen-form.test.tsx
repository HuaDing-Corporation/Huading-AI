import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const taskMocks = vi.hoisted(() => ({ createAndTrack: vi.fn() }));
vi.mock("@/lib/videos/tasks-context", () => ({ useVideoTasks: () => taskMocks }));
// 子组件占位为标记，隔离测表单编排（参考图/BGM 各有专测）。
vi.mock("@/components/workbench/reference-images-picker", () => ({
  ReferenceImagesPicker: ({ onChange }: { onChange: (ids: string[]) => void }) => (
    <button type="button" onClick={() => onChange(["a1", "a2"])}>
      set-refs
    </button>
  )
}));
vi.mock("@/components/workbench/bgm-picker", () => ({
  BgmPicker: ({ onChange }: { onChange: (b: unknown) => void }) => (
    <>
      <button type="button" onClick={() => onChange({ source: "library", track_id: "bgm-uplift" })}>
        set-bgm
      </button>
      <button type="button" onClick={() => onChange(undefined)}>
        clear-bgm
      </button>
    </>
  )
}));
// 确认窗占位：open 时渲染「确定」按钮触发 onConfirm（真 useGenerateConfirm 驱动 submit）。
vi.mock("@/components/workbench/confirm-generate-dialog", () => ({
  ConfirmGenerateDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <button type="button" onClick={onConfirm}>
        确定
      </button>
    ) : null
}));

import { VideoGenForm } from "./video-gen-form";

const setPrompt = (v: string) => fireEvent.change(screen.getByPlaceholderText(copy.workbench.vgPromptPlaceholder), { target: { value: v } });
const generateBtn = () => screen.getByRole("button", { name: copy.workbench.generate });

beforeEach(() => {
  taskMocks.createAndTrack.mockResolvedValue("task-1");
});
afterEach(() => vi.clearAllMocks());

describe("VideoGenForm (视频生成 编排)", () => {
  it("生成禁用直到 参考图 + prompt 齐全（带分步提示）", () => {
    render(<VideoGenForm />);
    expect(generateBtn()).toBeDisabled();
    expect(screen.getByText(copy.workbench.vgRefImagesRequired)).toBeInTheDocument();

    fireEvent.click(screen.getByText("set-refs"));
    expect(generateBtn()).toBeDisabled();
    expect(screen.getByText(copy.workbench.vgPromptRequired)).toBeInTheDocument();

    setPrompt("赛博城市夜景，霓虹运镜");
    expect(generateBtn()).toBeEnabled();
  });

  it("提交体：video_mode=video_gen + prompt + 参考图 + 默认时长5/分辨率720p；topic=prompt；无 BGM", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("赛博城市夜景");
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    const [request, topic] = taskMocks.createAndTrack.mock.calls[0];
    expect(request).toMatchObject({
      topic: "赛博城市夜景",
      prompt: "赛博城市夜景",
      video_mode: "video_gen",
      reference_image_asset_ids: ["a1", "a2"],
      duration_sec: 5,
      resolution: "720p"
    });
    expect(request.bgm).toBeUndefined();
    expect(topic).toBe("赛博城市夜景");
  });

  it("选时长 10 + 分辨率 480p → 提交体随之", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.durationSeconds(10) }));
    fireEvent.click(screen.getByRole("button", { name: "480P" }));
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({ duration_sec: 10, resolution: "480p" });
  });

  it("选配乐库 BGM → 提交体含 bgm{source:library,track_id}", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByText("set-bgm"));
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({
      video_mode: "video_gen",
      bgm: { source: "library", track_id: "bgm-uplift" }
    });
  });

  it("BGM 由有到无 → 提交体不含 bgm", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByText("set-bgm"));
    fireEvent.click(screen.getByText("clear-bgm"));
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].bgm).toBeUndefined();
  });
});
