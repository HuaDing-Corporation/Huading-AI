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
  window.localStorage.clear(); // 每用例干净起点：AI 标识开关默认关
  taskMocks.createAndTrack.mockResolvedValue("vid-1");
});
afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear(); // 清 AI 标识开关记忆，隔离用例
});

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
      resolution: "720p",
      aspect_ratio: "auto", // 需求3：默认自适应（BE API 值=auto，worker 翻译成 provider 的 adaptive），总随请求传
      generate_audio: false // 需求4：默认关，总随请求传（零回归）
    });
    expect(request.bgm).toBeUndefined();
    expect(request).not.toHaveProperty("negative_prompt"); // 需求1：空则不带
    expect(topic).toBe("赛博城市夜景");
  });

  // 🔴 需求2/D4 承重（关键）：提示词恰 2000 通过、2001 拦住（红字 + 生成禁用，不发请求）。
  // 变异：删 video-gen-form 的 promptOverLimit 判据 → 本条红（2001 时按钮仍 enabled / 提交发出）。
  it("提示词 2000 通过 / 2001 拦住（红字 + 生成禁用，不发请求）", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    // 恰 2000 → 可生成、无红字。
    setPrompt("x".repeat(2000));
    expect(screen.queryByText(new RegExp(copy.workbench.vgPromptOverLimit))).not.toBeInTheDocument();
    expect(generateBtn()).toBeEnabled();
    // 2001 → 红字 + 生成禁用。
    setPrompt("x".repeat(2001));
    // Code Review：超限文案=用户原话 + 括号附实际计数（2001 / 2000），故用正则子串匹配 + 单独断言计数在场。
    expect(screen.getByText(new RegExp(copy.workbench.vgPromptOverLimit))).toBeInTheDocument();
    expect(screen.getByText(/2001 \/ 2000/)).toBeInTheDocument();
    expect(generateBtn()).toBeDisabled();
  });

  // 🔴 需求5 承重：时长自定义越界(3/16)/小数(5.5)拦住（不发）；合法整数(8)通过并随请求传。
  // 变异：把 isValidDuration 区间改回宽松 / 去掉 durationValid 判据 → 本条红。
  it("时长自定义：3/16/5.5 拦住，8 通过并随请求传", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.durationCustom }));
    const customInput = screen.getByLabelText(copy.workbench.durationCustomLabel);
    for (const bad of ["3", "16", "5.5"]) {
      fireEvent.change(customInput, { target: { value: bad } });
      expect(generateBtn()).toBeDisabled();
    }
    fireEvent.change(customInput, { target: { value: "8" } });
    expect(generateBtn()).toBeEnabled();
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].duration_sec).toBe(8);
  });

  // 🔴 需求4 承重：开启音频开关 → 提交体 generate_audio:true。变异：onGenerate 丢 generate_audio 字段 → 本条红。
  it("开启音频生成开关 → 提交体 generate_audio:true", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByRole("switch", { name: copy.workbench.vgAudioToggleAria }));
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].generate_audio).toBe(true);
  });

  // 需求3 承重：画面比例默认 adaptive、可切显式比例 → 提交体随之（下拉用 combobox 选 16:9）。
  it("画面比例：默认 adaptive，切 16:9 → 提交体 aspect_ratio:16:9", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByRole("combobox", { name: new RegExp(copy.workbench.vgAspectLabel) }));
    fireEvent.click(await screen.findByRole("option", { name: "16:9" }));
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].aspect_ratio).toBe("16:9");
  });

  // 需求1 承重：负面提示词填写 → 提交体带 negative_prompt。
  it("填负面提示词 → 提交体 negative_prompt", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.change(screen.getByPlaceholderText(copy.workbench.vgNegativePlaceholder), { target: { value: "水印、变形" } });
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].negative_prompt).toBe("水印、变形");
  });

  it("默认关：提交体 apply_visible_label:false（AI 标识默认关）", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(false);
  });

  it("开启 AI 标识开关 → 提交体 apply_visible_label:true（承重）", async () => {
    render(<VideoGenForm />);
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByRole("switch", { name: "AI 生成标识" })); // 消歧：另有「音频生成开关」
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));
    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0].apply_visible_label).toBe(true);
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

  it("选分辨率 1080P → 提交体 resolution:1080p（VIDEO-UI-1080P-0001；默认档不变）", async () => {
    render(<VideoGenForm />);
    // 三档均在，默认仍选 720P（不改默认）。
    expect(screen.getByRole("button", { name: "480P" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1080P" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("set-refs"));
    setPrompt("p");
    fireEvent.click(screen.getByRole("button", { name: "1080P" }));
    fireEvent.click(generateBtn());
    fireEvent.click(await screen.findByRole("button", { name: "确定" }));

    await waitFor(() => expect(taskMocks.createAndTrack).toHaveBeenCalledTimes(1));
    expect(taskMocks.createAndTrack.mock.calls[0][0]).toMatchObject({ resolution: "1080p" });
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
