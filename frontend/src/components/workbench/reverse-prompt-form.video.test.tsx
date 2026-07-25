import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { copy } from "@/lib/copy";
import type { ReversePromptEstimate, ReversePromptJobRead } from "@/lib/api/reverse-prompt";

// VIDEO-REVERSE-PROMPT-UI-0001 · 视频反推路径 TDD。隔离网络：mock hooks + getReversePromptJob（轮询）+
// validateReverseVideo（jsdom 读不到视频元数据，直接判合规）。锁：图片/视频切换、上传、计费门恰一次/取消不扣、
// 异步轮询、结果(video_analysis + prompt)、重新反推隐藏（防二次扣费）。isReverseSettled/friendlyReverseError/常量保留真实。
const hooks = vi.hoisted(() => ({
  useUploadImage: vi.fn(),
  useUploadReverseVideo: vi.fn(),
  useReverseFromAsset: vi.fn(),
  useRegenerateReversePrompt: vi.fn(),
  useSaveReversePrompt: vi.fn(),
  useEstimateReversePrompt: vi.fn()
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

// FIX2：pacing 合法枚举（非中文串）；每个 shot 含 index（对齐真 BE ReversePromptShot/VideoAnalysis）。
const VIDEO_ANALYSIS = {
  duration_sec: 18,
  pacing: "fast" as const,
  shot_list: [
    { index: 0, start_sec: 0, end_sec: 4, visual: "产品特写", camera: "推近", motion: "蒸汽", transition: "叠化" },
    { index: 1, start_sec: 4, end_sec: 10, visual: "使用场景", camera: "跟拍", motion: "拧盖", transition: "硬切" }
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
    ecom_model: { extra_prompt: "白底" }
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
const estimateMut = vi.fn();
const estimateReset = vi.fn();
/**
 * 计费预估的可变桩态（§八 M4）。**用 mockImplementation 每次渲染重新读**，这样测试中途改档位/改成失败态
 * 都能被下一次渲染看到 —— 若用 mockReturnValue 会把首帧那个对象钉死，「估算失败」这类用例根本进不去。
 * 默认：短档 100 积分（对齐 mock 里 3s 视频的 video_short）。
 */
const estimateState: {
  data: ReversePromptEstimate | undefined;
  /** 这份报价是给哪条资产估的（TanStack mutation 的 variables）——「报价属于当前素材」的判据。 */
  variables: { source_asset_id: string } | undefined;
  isPending: boolean;
  isError: boolean;
} = {
  data: { credits: 100, duration_sec: 3, tier: "video_short" },
  variables: { source_asset_id: "video-asset-1" },
  isPending: false,
  isError: false
};
function stub() {
  (hooks.useUploadImage as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "aid-1" }), isPending: false });
  (hooks.useUploadReverseVideo as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "video-asset-1" }), isPending: false });
  (hooks.useReverseFromAsset as Mock).mockReturnValue({ mutateAsync: reverseMut, isPending: false });
  (hooks.useRegenerateReversePrompt as Mock).mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  (hooks.useSaveReversePrompt as Mock).mockReturnValue({ mutateAsync: vi.fn().mockResolvedValue({ saved: true }), isPending: false });
  (hooks.useEstimateReversePrompt as Mock).mockImplementation(() => ({
    mutate: estimateMut,
    reset: estimateReset,
    data: estimateState.data,
    variables: estimateState.variables,
    isPending: estimateState.isPending,
    isError: estimateState.isError
  }));
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
  estimateState.data = { credits: 100, duration_sec: 3, tier: "video_short" }; // 每条用例回到默认短档
  estimateState.variables = { source_asset_id: "video-asset-1" }; // 与 useUploadReverseVideo 桩返回的 asset 一致
  estimateState.isPending = false;
  estimateState.isError = false;
  stub();
  media.validateReverseVideo.mockResolvedValue(null); // 合规
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});
afterEach(() => vi.clearAllMocks());

describe("ReversePromptForm · 视频反推路径", () => {
  it("切「视频」→ 视频标题/上传 + 定性计费角标（不写死金额）；图片入口零回归（默认图片）", () => {
    render(<ReversePromptForm />);
    // 默认图片
    expect(screen.getByText(copy.reverse.subtitle)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.reverse.sourceVideo) }));
    expect(screen.getByText(copy.reverse.videoSubtitle)).toBeInTheDocument();
    // D9 分档后徽标在上传前无从判档 → 只做定性说明，**界面上不许出现任何积分数字**。
    const badge = screen.getByText(copy.reverse.videoChargeBadge);
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).not.toMatch(/\d/);
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

  // FIX2 P2：计费门防二次扣费——快速连点确认两次，只调一次 reverse（submitting guard + 按钮 disabled）。
  it("计费门防连点：确认按钮连点两次 → reverse 只调一次、只扣一次", async () => {
    reverseMut.mockResolvedValue(queuedJob());
    api.getReversePromptJob.mockResolvedValue(succeededJob());
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    const confirm = await screen.findByRole("button", { name: copy.reverse.videoChargeConfirm });
    fireEvent.click(confirm);
    fireEvent.click(confirm); // 快速二次确认应被拦截
    await waitFor(() => expect(reverseMut).toHaveBeenCalledTimes(1));
    expect(reverseMut).toHaveBeenCalledTimes(1);
  });

  // ══ REVERSE-DEEP-UI-0001-FIX1 · 承重门 9–11 ═══════════════════════════════════════════════
  // 承重门9「报价与实扣同源」：弹窗金额必须是 estimate 返回的那个数。
  // 🔴 变异点就在 reverse-prompt-form.tsx 的 `message={estimate.data ? videoChargeMessage(estimate.data.credits) : …}`：
  //    把它改回写死 100，本条（长档 250）立刻红 —— 这正是「报价 100、实扣 250」事故的护栏。
  it("🔴 承重门9 延伸 · 上一条视频的报价不许沿用到当前素材（Code Review 自审 P1）", async () => {
    // TanStack mutation 重跑时不清 data → 若判据只看「有 data」，换视频后新报价回来之前会显示旧金额。
    // 这里模拟那一帧：data 还是旧的 100，但它是给 video-asset-OLD 估的，而当前素材是 video-asset-1。
    estimateState.data = { credits: 100, duration_sec: 3, tier: "video_short" };
    estimateState.variables = { source_asset_id: "video-asset-OLD" };
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    const dialog = await screen.findByRole("dialog");
    // 对不上就当没报价：不显示任何金额、也不许提交（变异：删掉 quote 里的资产比对 → 本条红）
    expect(dialog.textContent ?? "").not.toMatch(/\d/);
    expect(within(dialog).getByRole("button", { name: copy.reverse.videoChargeConfirm })).toBeDisabled();
  });

  it("承重门9 · 报价与实扣同源：长档显示 estimate 返回的 250，不是写死的 100", async () => {
    estimateState.data = { credits: 250, duration_sec: 180, tier: "video_long" };
    estimateState.variables = { source_asset_id: "video-asset-1" }; // 就是当前这条素材的报价
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    // 打开计费门就必须去 BE 取价（而且带的是本次这条资产，不是随便一个 id）
    await waitFor(() => expect(estimateMut).toHaveBeenCalledWith({ source_asset_id: "video-asset-1" }));
    expect(await screen.findByText(copy.reverse.videoChargeMessage(250))).toBeInTheDocument();
    expect(screen.queryByText(copy.reverse.videoChargeMessage(100))).not.toBeInTheDocument();
  });

  // 承重门10「estimate 失败不猜数」：不显示任何金额、且挡住提交（宁可挡住也不能报错价）。
  it("承重门10 · estimate 失败：弹窗不出现任何积分数字，确认按钮禁用且点不动", async () => {
    estimateState.data = undefined;
    estimateState.isError = true;
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(copy.errors.reverseEstimateFailed)).toBeInTheDocument();
    // 🔴 断的是「整个弹窗里没有任何数字」，而不是「没有 100」——兜底猜一个 88 也必须红。
    expect(dialog.textContent ?? "").not.toMatch(/\d/);
    const confirm = within(dialog).getByRole("button", { name: copy.reverse.videoChargeConfirm });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(reverseMut).not.toHaveBeenCalled();
  });

  // 承重门11「分段进度」：文案跟着 segments_done 走。
  // 🔴 FIX2 真联调订正语义：BE 的 `segments_done` 是**已完成段数**，进入第 N 段前写的是 N-1
  //    （证据 backend/tests/test_reverse_prompt_pipeline.py:1860）。上一版这条断言 done=2 → 显示「第 2/6 段」，
  //    钉的其实是**错的**语义 —— 真机第一段进行中 done=0，界面会显示「正在分析第 0/6 段」。
  //    改后：序号 = done + 1（封顶 total）。
  it("承重门11 · 分段进度：序号 = 已完成数 + 1，随 segments_done 推进而变", async () => {
    reverseMut.mockResolvedValue({ ...queuedJob(), segments_total: 6, segments_done: 0 });
    api.getReversePromptJob
      .mockResolvedValueOnce({ ...queuedJob(), status: "running", segments_total: 6, segments_done: 0 })
      .mockResolvedValueOnce({ ...queuedJob(), status: "running", segments_total: 6, segments_done: 2 })
      .mockResolvedValue(succeededJob());
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.videoChargeConfirm }));
    // 🔴 done=0（第一段进行中）→ 必须是「第 1/6 段」，**绝不能是「第 0/6 段」**
    expect(await screen.findByText(copy.reverse.videoSegmentProgress(1, 6), {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(/第 0\/6 段/)).not.toBeInTheDocument();
    expect(screen.getByText(copy.reverse.videoSegmentEta)).toBeInTheDocument();
    // done=2 → 第 3/6 段（跟着推进）
    expect(await screen.findByText(copy.reverse.videoSegmentProgress(3, 6), {}, { timeout: 3000 })).toBeInTheDocument();
  });

  // 承重门11 的第三面：末段跑完（done == total）后 BE 还要走一次整片汇总，此时序号必须**封顶**在 total，
  // 不能显示「第 7/6 段」。变异：把 Math.min(done + 1, total) 改成 done + 1 → 本条必红。
  it("承重门11 · done == total（整片汇总中）→ 显示第 6/6 段，不越界成 7/6", async () => {
    reverseMut.mockResolvedValue({ ...queuedJob(), segments_total: 6, segments_done: 0 });
    api.getReversePromptJob.mockResolvedValue({
      ...queuedJob(),
      status: "running",
      segments_total: 6,
      segments_done: 6
    });
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.videoChargeConfirm }));
    expect(await screen.findByText(copy.reverse.videoSegmentProgress(6, 6), {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText(/第 7\/6 段/)).not.toBeInTheDocument();
  });

  // 承重门11 的另一半：两字段为 null（图片 / ≤60s 短视频）→ **整块不渲染**，形态与改动前一致。
  it("承重门11 · segments 为 null：不渲染分段进度，不做假进度条", async () => {
    reverseMut.mockResolvedValue(queuedJob()); // 无 segments_*
    api.getReversePromptJob.mockResolvedValue({ ...queuedJob(), status: "running" }); // 一直在跑
    await switchToVideoAndUpload();
    fireEvent.click(screen.getByRole("button", { name: copy.reverse.videoAnalyze }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.videoChargeConfirm }));
    // 真轮询过（不是「还没开始所以没渲染」的假绿）
    await waitFor(() => expect(api.getReversePromptJob).toHaveBeenCalled(), { timeout: 3000 });
    expect(screen.queryByText(copy.reverse.videoSegmentEta)).not.toBeInTheDocument();
    expect(screen.queryByText(/正在分析第/)).not.toBeInTheDocument();
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
