import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { copy } from "@/lib/copy";
import type { ReversePromptJobRead } from "@/lib/api/reverse-prompt";

// VIDEO-REVERSE-PROMPT-UI-0001 · 视频反推路径 TDD。隔离网络：mock hooks + getReversePromptJob（轮询）+
// validateReverseVideo（jsdom 读不到视频元数据，直接判合规）。锁：图片/视频切换、上传、计费门恰一次/取消不扣、
// 异步轮询、结果(video_analysis + prompt)、重新反推隐藏（防二次扣费）。isReverseSettled/friendlyReverseError/常量保留真实。
const hooks = vi.hoisted(() => ({
  useUploadImage: vi.fn(),
  useUploadReverseVideo: vi.fn(),
  useReverseFromAsset: vi.fn(),
  useRegenerateReversePrompt: vi.fn(),
  useSaveReversePrompt: vi.fn()
}));
vi.mock("@/lib/api/hooks", () => hooks);

const api = vi.hoisted(() => ({ getReversePromptJob: vi.fn() }));
vi.mock("@/lib/api/reverse-prompt", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/reverse-prompt")>();
  return { ...actual, ...api };
});

const media = vi.hoisted(() => ({ validateReverseVideo: vi.fn() }));
vi.mock("@/lib/media/reverse-video", () => media);

import { ReversePromptForm } from "./reverse-prompt-form";

const VIDEO_ANALYSIS = {
  duration_sec: 18,
  pacing: "中速偏快",
  shot_list: [
    { start_sec: 0, end_sec: 4, visual: "产品特写", camera: "推近", motion: "蒸汽", transition: "叠化" },
    { start_sec: 4, end_sec: 10, visual: "使用场景", camera: "跟拍", motion: "拧盖", transition: "硬切" }
  ],
  audio_transcript: null,
  bgm_style: null
};
const RESULT = {
  target_format: "seedance_2_0",
  prompt_zh: "视频反推中文提示词VVV",
  prompt_en: "video en prompt",
  negative_prompt: "水印",
  style_tags: ["产品广告"],
  camera: "",
  lighting: "",
  composition: "",
  subject: "保温杯",
  scene: "",
  motion_hint: "",
  selling_points: [],
  text_in_media: [],
  disclaimer: "",
  confidence: 0.8,
  fill_targets: {
    avatar_talk: { topic: "杯", script: "大家好" },
    seedance_i2v: { topic: "杯", scene_prompt: "暖光" },
    video_gen: { topic: "杯", prompt: "运镜" },
    photo: { topic: "白底杯" },
    ecom_model: { extra_prompt: "白底" },
    ecom_poster: { title: "促", subtitle: "5 折" }
  }
};
// FIX1③：初始 queued（BE 202 queued，非 running）；④ credits=provider 引擎成本（非租户 100 扣费）。
const queuedJob = (): ReversePromptJobRead => ({
  id: "rpv-1", status: "queued", source_kind: "video", target_format: "seedance_2_0", result: null,
  error_code: null, error_message: null, prompt_tokens: 0, completion_tokens: 0, credits: 6, cost_cents: 0,
  created_at: "1970-01-01T00:00:00Z", updated_at: "1970-01-01T00:00:00Z", saved_at: null
});
// FIX1①：video_analysis 内嵌于 result（result.video_analysis）。
const succeededJob = (): ReversePromptJobRead => ({ ...queuedJob(), status: "succeeded", result: { ...RESULT, video_analysis: VIDEO_ANALYSIS } });

const reverseMut = vi.fn();
function stub() {
  (hooks.useUploadImage as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "aid-1" }), isPending: false });
  (hooks.useUploadReverseVideo as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "video-asset-1" }), isPending: false });
  (hooks.useReverseFromAsset as Mock).mockReturnValue({ mutateAsync: reverseMut, isPending: false });
  (hooks.useRegenerateReversePrompt as Mock).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  (hooks.useSaveReversePrompt as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ saved: true }), isPending: false });
}

function selectVideo() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "v.mp4", { type: "video/mp4" })] } });
}

async function switchToVideoAndUpload() {
  render(<ReversePromptForm />);
  fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.reverse.sourceVideo) }));
  selectVideo();
  await waitFor(() => expect(screen.getByRole("button", { name: copy.reverse.videoAnalyze })).toBeEnabled());
}

beforeEach(() => {
  vi.clearAllMocks();
  stub();
  media.validateReverseVideo.mockResolvedValue(null); // 合规
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});
afterEach(() => vi.clearAllMocks());

describe("ReversePromptForm · 视频反推路径", () => {
  it("切「视频」→ 视频标题/上传 + 100 积分角标；图片入口零回归（默认图片）", () => {
    render(<ReversePromptForm />);
    // 默认图片
    expect(screen.getByText(copy.reverse.subtitle)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.reverse.sourceVideo) }));
    expect(screen.getByText(copy.reverse.videoSubtitle)).toBeInTheDocument();
    expect(screen.getByText(copy.reverse.videoChargeBadge(100))).toBeInTheDocument();
  });

  it("视频上传预检不合规 → 友好中文，不触发上传", async () => {
    media.validateReverseVideo.mockResolvedValue(copy.errors.reverseVideoDuration);
    const uploadVid = { mutateAsync: vi.fn(), isPending: false };
    (hooks.useUploadReverseVideo as Mock).mockReturnValue(uploadVid);
    render(<ReversePromptForm />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.reverse.sourceVideo) }));
    selectVideo();
    expect(await screen.findByText(copy.errors.reverseVideoDuration)).toBeInTheDocument();
    expect(uploadVid.mutateAsync).not.toHaveBeenCalled();
  });

  it("计费门：反推 → 确认弹窗(100 积分)；取消 → 不发 reverse、不扣费", async () => {
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    expect(await screen.findByText(copy.reverse.videoChargeMessage(100))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));
    expect(reverseMut).not.toHaveBeenCalled();
  });

  it("计费门：确认 → reverse 恰一次(source_asset_id)；轮询至完成 → 视频分析 + 提示词 + 重新反推隐藏", async () => {
    reverseMut.mockResolvedValue(queuedJob());
    api.getReversePromptJob.mockResolvedValue(succeededJob());
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.videoChargeConfirm }));
    await waitFor(() => expect(reverseMut).toHaveBeenCalledWith({ source_asset_id: "video-asset-1" }));
    expect(reverseMut).toHaveBeenCalledTimes(1);
    // 轮询(1500ms)后：视频分析在上（vaTitle + 分镜）+ Seedance 提示词在下。
    expect(await screen.findByText(copy.reverse.vaTitle, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText("视频反推中文提示词VVV")).toBeInTheDocument();
    // 资金安全：视频结果隐藏「重新反推」（防二次扣费）；「带入」仍在。
    expect(screen.queryByRole("button", { name: copy.reverse.regenerate })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.reverse.applyAvatar })).toBeInTheDocument();
  });

  it("轮询瞬时失败 → 软提示、不误跳结果页；下一拍自愈后展示", async () => {
    reverseMut.mockResolvedValue(queuedJob());
    api.getReversePromptJob.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(succeededJob());
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.videoChargeConfirm }));
    // 首拍失败 → 软提示，未出结果。
    expect(await screen.findByText(copy.reverse.videoPollRetrying, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(copy.reverse.vaTitle)).not.toBeInTheDocument();
    // 次拍自愈 → 结果。
    expect(await screen.findByText(copy.reverse.vaTitle, {}, { timeout: 4000 })).toBeInTheDocument();
  });
});
