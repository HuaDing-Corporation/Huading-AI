import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * REVERSE-DEEP-UI-0001 · **承重门 1/2/3**（page 级端到端，真 page + 真目标表单）：
 *  门1 逐字段直落：video_gen / photo / seedance_i2v 各一条 —— 断言目标表单的负面提示词框 / 比例 / 时长
 *      拿到**确定值**（toBe，不是「非空」）。
 *  门2 取消勾选 = 控件不动：先在目标表单填一个值 → 带入时取消该项 → 断言**原值一字未变**（不是被清空）。
 *  门3 clamp 提示：duration_clamped=true 时那行提示**真的渲染**，且落进控件的是 clamp 后的合法值。
 * 走的是完整真实链路：反推结果视图 → 带入确认弹窗 → page.injectPrefill → 目标表单 useEffect 同步 props。
 */
vi.mock("next/navigation", () => ({ useRouter: () => ({ back: vi.fn(), push: vi.fn() }) }));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { role: "admin", user: { permissions: [] } }, ready: true })
}));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: [], createAndTrack: vi.fn(), refreshTask: vi.fn(), retryTask: vi.fn() })
}));
vi.mock("@/components/layout/top-bar", () => ({ TopBar: () => <div data-testid="topbar" /> }));
vi.mock("@/components/contact/welcome-contact-banner", () => ({ WelcomeContactBanner: () => null }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
vi.mock("@/components/tasks/task-list", () => ({ TaskList: () => <div data-testid="tasklist" /> }));
vi.mock("@/components/tasks/generation-history", () => ({ GenerationHistory: () => <div data-testid="history" /> }));

const reverseMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/lib/api/hooks", () => ({
  // 反推表单
  useUploadImage: () => ({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "aid-1" }), isPending: false }),
  useUploadReverseVideo: () => ({ mutateAsync: vi.fn().mockResolvedValue({ asset_id: "video-asset-1" }), isPending: false }),
  useReverseFromAsset: () => ({ mutateAsync: reverseMock.mutateAsync, isPending: false }),
  useRegenerateReversePrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSaveReversePrompt: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // §八 M4 计费预估（本文件只走图片路径、不开计费门，给个短档常态桩即可）
  useEstimateReversePrompt: () => ({
    mutate: vi.fn(),
    reset: vi.fn(),
    data: { credits: 100, duration_sec: 3, tier: "video_short" },
    isPending: false,
    isError: false
  }),
  // 目标表单
  useUploadProductImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadVideoGenReference: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAudio: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBgmLibrary: () => ({ data: [], isLoading: false }),
  useScriptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScenePromptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({ data: [{ id: "v1", provider: "edge", voice_code: "x", display_name: "音色1", gender: null, language: null }] }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({ data: [] }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: { estimated_credits: 8, unit: "credits" } })
}));

import { copy } from "@/lib/copy";
import type { ReversePromptJobRead, ReversePromptResult } from "@/lib/api/reverse-prompt";
import Home from "./page";

// 确定值常量 —— 断言逐字比对这些串（不是「非空」）。
const NEG = "低分辨率, 变形, 多余文字, 水印, 杂乱背景";
const MASTER = "统一走高级产品广告质感，干净背景";
const STRUCTURED_EN = "Subject: bottle.\nScene: marble counter.\nStyle: product ad";
/** §八 M2：BE 拼好的完整分镜段（**含段头**），前端只拼不拆。 */
const SHOT_SECTION = "Shots:\n0-4s 产品特写；4-10s 使用场景。";

/** 视频源反推结果：video_gen 时长被 clamp 到 15（原 18s）；seedance 装得下 18s 不 clamp。 */
const RESULT: ReversePromptResult = {
  target_format: "seedance_2_0",
  subject: "保温杯",
  scene: "暖光桌面",
  composition: "居中",
  camera: "35mm",
  lighting: "柔光",
  style_tags: ["极简"],
  motion_hint: "环绕",
  prompt_zh: "中文提示词ZZZ",
  prompt_en: "en prompt",
  negative_prompt: NEG,
  selling_points: [],
  text_in_media: [],
  disclaimer: "",
  confidence: 0.8,
  source_media: { kind: "video", width: 1080, height: 1920, duration_sec: 18, aspect_ratio_raw: "9:16" },
  structured_prompt: { en: STRUCTURED_EN, zh: "主体：保温杯" },
  video_analysis: { duration_sec: 18, pacing: "fast", shot_list: [], audio_transcript: null, bgm_style: null },
  fill_targets: {
    avatar_talk: { topic: "保温杯种草", script: "大家好" },
    seedance_i2v: {
      topic: "卖点",
      scene_prompt: "暖光",
      negative_prompt: NEG,
      duration_sec: 18, // 5–120 装得下 → 不 clamp
      duration_clamped: false
    },
    video_gen: {
      topic: "杯",
      prompt: STRUCTURED_EN,
      negative_prompt: NEG,
      shot_section: SHOT_SECTION, // §八 M2：独立键（含段头），主提示词里**不含**它
      aspect_ratio: "9:16",
      duration_sec: 15, // 原 18s 超 4–15 上限 → clamp 到 15
      duration_clamped: true,
      generate_audio: false
    },
    // ⚠️ master_prompt：BE 真机**恒 None**（services:770 硬编码）。这里刻意仍给一个真串 ——
    //    本文件是**契约允许形态**的单测夹具，用来保住 masterPrompt 那条映射分支的覆盖（BE 日后填值即生效）；
    //    而 MSW mock（handlers.ts）已按真机形态改成 null。两者分工不同，不要"统一"。
    photo: { topic: STRUCTURED_EN, master_prompt: MASTER, negative_prompt: NEG, aspect_ratio: "9:16" },
    ecom_model: { extra_prompt: "白底", aspect_ratio: "3:4" }
    // FIX2 真联调：BE fill_targets 无 ecom_poster（自测 tests:2713 显式断言）→ 夹具一并删除。
  }
};
const JOB: ReversePromptJobRead = {
  id: "rp-1",
  status: "succeeded",
  source_kind: "video",
  target_format: "seedance_2_0",
  result: RESULT,
  error_code: null,
  error_message: null,
  prompt_tokens: 0,
  completion_tokens: 0,
  credits: 0,
  cost_cents: 0,
  created_at: "1970-01-01T00:00:00Z",
  updated_at: "1970-01-01T00:00:00Z",
  saved_at: null
};

beforeEach(() => {
  vi.clearAllMocks();
  reverseMock.mutateAsync.mockResolvedValue(JOB);
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  window.localStorage.clear();
});

const panel = (mode: string) => within(screen.getByTestId(`panel-${mode}`));

/**
 * 切到反推模式 → 传图 → 反推 → 等结果出来。
 * ⚠️ 面板「挂载后常驻」→ DOM 里同时存在多个表单的 file input，**必须按面板取作用域**
 *    （裸 document.querySelector 会抓到默认口播面板的那个，反推永远传不上图）。
 */
async function produceResult() {
  fireEvent.click(screen.getByRole("button", { name: "提示词反推" }));
  const rp = screen.getByTestId("panel-reverse_prompt");
  const input = rp.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
  const analyze = within(rp).getByRole("button", { name: copy.reverse.analyze });
  await waitFor(() => expect(analyze).toBeEnabled());
  fireEvent.click(analyze);
  // 结果落地判据：有 structured_prompt 时结果视图展示的是**结构化中文版**（范围4），不再是 prompt_zh。
  await screen.findByText(RESULT.structured_prompt!.zh);
}

/** 打开带入确认弹窗（不确认）。 */
const openApplyDialog = (label: string) => fireEvent.click(screen.getByRole("button", { name: label }));
const confirmApply = () => fireEvent.click(screen.getByRole("button", { name: copy.reverse.applyConfirmSubmit }));
/** 弹窗里按可及名取某项的勾选框。 */
const itemCheckbox = (label: string) => screen.getByRole("checkbox", { name: label });

describe("REVERSE-DEEP · 承重门1 逐字段直落（确定值）", () => {
  it("🔴 带入 · 视频生成 → 负面提示词 / 画面比例 / 时长 三个控件都拿到确定值", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);
    confirmApply();

    const vg = panel("video_gen");
    // 负面提示词框：逐字确定值
    await waitFor(() =>
      expect((vg.getByLabelText(copy.workbench.vgNegativeLabel) as HTMLTextAreaElement).value).toBe(NEG)
    );
    // 时长：clamp 后的 15 秒档被选中
    expect(vg.getByRole("button", { name: /15\s*秒/ })).toHaveAttribute("aria-pressed", "true");
    // 画面比例：触发器上显示 9:16
    expect(vg.getByLabelText(copy.workbench.vgAspectLabel).textContent).toContain("9:16");
  });

  // 承重门12 的端到端一半（弹窗侧在 prefill-confirm-dialog.test.tsx）：
  // 🔴 断的是**真正落进目标表单控件里的那个串** —— 只在弹窗里断，拼对了却没落进控件也照样绿。
  it("🔴 承重门12 · 带入 · 视频生成 → 提示词框拿到「主提示词 + BE 分镜段」的拼接结果", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);
    confirmApply();

    const vg = panel("video_gen");
    await waitFor(() =>
      expect((vg.getByLabelText(copy.workbench.vgPromptLabel) as HTMLTextAreaElement).value).toBe(
        `${STRUCTURED_EN}\n\n${SHOT_SECTION}`
      )
    );
  });

  it("🔴 承重门12 · 取消「分镜表」→ 落进控件的提示词不含分镜段（开关在真链路上也有用）", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);
    fireEvent.click(itemCheckbox(copy.reverse.applyItemShots));
    confirmApply();

    const vg = panel("video_gen");
    await waitFor(() =>
      expect((vg.getByLabelText(copy.workbench.vgPromptLabel) as HTMLTextAreaElement).value).toBe(STRUCTURED_EN)
    );
  });

  it("🔴 带入 · 图片生成 → 图片负面提示词 / 总控前缀 / 画面比例 都拿到确定值", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyPhoto);
    confirmApply();

    const ph = panel("photo");
    await waitFor(() =>
      expect((ph.getByLabelText(copy.workbench.imageNegativeLabel) as HTMLTextAreaElement).value).toBe(NEG)
    );
    expect((ph.getByLabelText(copy.workbench.masterPromptLabel) as HTMLTextAreaElement).value).toBe(MASTER);
    expect(ph.getByLabelText(copy.workbench.aspectLabel).textContent).toContain("9:16");
  });

  it("🔴 带入 · 电商带货 → 负面提示词 / 时长 拿到确定值（本模块区间装得下 18s，不 clamp）", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyEcomVideo);
    confirmApply();

    const ec = panel("seedance_i2v");
    await waitFor(() =>
      expect((ec.getByLabelText(copy.workbench.negativePromptLabel) as HTMLTextAreaElement).value).toBe(NEG)
    );
    // 18 不是预设档 → 落进「自定义」数字输入
    expect((ec.getByLabelText(copy.workbench.durationCustomLabel) as HTMLInputElement).value).toBe("18");
  });
});

describe("REVERSE-DEEP · 承重门2 取消勾选 = 控件保持原样（不是清空）", () => {
  it("🔴 用户已填负面提示词 → 带入时取消该项 → 原值**一字未变**（变异：改回无条件写空串 → 本条红）", async () => {
    render(<Home />);
    // 先让用户在视频生成表单里填一个自己的负面提示词
    fireEvent.click(screen.getByRole("button", { name: "视频生成" }));
    const mine = "我自己写的负面词";
    fireEvent.change(panel("video_gen").getByLabelText(copy.workbench.vgNegativeLabel), { target: { value: mine } });

    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);
    // 取消「负面提示词」这一项 → 该键不下发 → 表单跳过 → 控件保持原样
    fireEvent.click(itemCheckbox(copy.reverse.applyItemNegative));
    confirmApply();

    const vg = panel("video_gen");
    // 其它项照常落值（证明带入确实发生了，不是整体没生效）
    await waitFor(() => expect(vg.getByRole("button", { name: /15\s*秒/ })).toHaveAttribute("aria-pressed", "true"));
    // 🔴 承重：负面提示词仍是用户自己那句，一字未变（既没被 BE 值覆盖，更没被空串洗掉）
    expect((vg.getByLabelText(copy.workbench.vgNegativeLabel) as HTMLTextAreaElement).value).toBe(mine);
  });

  it("取消勾选后该行显示「不带入（保持原样）」，用文字而非仅靠颜色表意", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);
    fireEvent.click(itemCheckbox(copy.reverse.applyItemNegative));
    expect(screen.getAllByText(copy.reverse.applyItemSkipped).length).toBeGreaterThan(0);
  });
});

describe("REVERSE-DEEP · 承重门3 clamp 提示（不许静默改数）", () => {
  it("🔴 duration_clamped=true → 弹窗渲染那行明确提示，且落进控件的是 clamp 后的 15 秒", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);

    // 提示逐字：原视频 18 秒，带入 · 视频生成单条上限 15 秒，已按上限带入
    expect(
      screen.getByText(copy.reverse.applyClampNote(18, "视频生成", 15))
    ).toBeInTheDocument();

    confirmApply();
    await waitFor(() =>
      expect(panel("video_gen").getByRole("button", { name: /15\s*秒/ })).toHaveAttribute("aria-pressed", "true")
    );
  });

  it("未 clamp 的模块（电商带货，区间装得下）→ 不显示 clamp 提示（不制造假警告）", async () => {
    render(<Home />);
    await produceResult();
    openApplyDialog(copy.reverse.applyEcomVideo);
    expect(screen.queryByText(/已按上限带入/)).not.toBeInTheDocument();
  });

  /**
   * 🔴 Code Review P1-3：上一条只证明「新挂载的时长控件显示 15」——把 duration-picker 的同步 effect 整个删掉，
   *    它照样绿（新挂载时 custom 本就是 false）。真正会坏的是**用户先进过自定义档**的情形：
   *    界面停在自定义 7，state 却被带入改成 15 → 用户看到 7、提交 15，正是 D8 禁止的「静默改数」。
   */
  it("🔴 用户先手输自定义时长 7 → 带入 clamp 后的 15（预设档）→ 控件**回到 15 秒档**，不再显示 7", async () => {
    render(<Home />);
    fireEvent.click(screen.getByRole("button", { name: "视频生成" }));
    const vg0 = panel("video_gen");
    fireEvent.click(vg0.getByRole("button", { name: copy.workbench.durationCustom }));
    fireEvent.change(vg0.getByLabelText(copy.workbench.durationCustomLabel), { target: { value: "7" } });
    expect((vg0.getByLabelText(copy.workbench.durationCustomLabel) as HTMLInputElement).value).toBe("7");

    await produceResult();
    openApplyDialog(copy.reverse.applyVideoGen);
    confirmApply();

    const vg = panel("video_gen");
    // 15 秒预设档被选中，且自定义输入框已收起（界面与将要提交的值一致）
    await waitFor(() => expect(vg.getByRole("button", { name: /15\s*秒/ })).toHaveAttribute("aria-pressed", "true"));
    expect(vg.queryByLabelText(copy.workbench.durationCustomLabel)).not.toBeInTheDocument();
  });
});
