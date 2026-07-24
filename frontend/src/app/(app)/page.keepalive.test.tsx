import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// WORKBENCH-KEEPALIVE-UI-0001 · 承重：工作台切模块不再丢当前模块的输入。
// 改造前 (app)/page.tsx 用条件渲染切 7 个表单 → 切 mode 即卸载 → React state 全销毁（提示词/上传图/参数全丢）。
// 改造后：某 mode 首次访问才挂载，此后仅用 hidden 隐藏、不卸载 → 输入保活。
// 本文件用**真实表单** + mock hooks 层跑真实 state，逐 mode 钉「填 → 切走 → 切回 → 原样还在」。
// 变异门：把 page.tsx 的常驻挂载改回条件渲染（或让隐藏面板卸载）→ 本文件必红。

vi.mock("next/navigation", () => ({ useRouter: () => ({ back: vi.fn(), push: vi.fn() }) }));
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { role: "admin", user: { permissions: ["voice_clone_vip"] } }, ready: true }) }));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: [], createAndTrack: vi.fn(), refreshTask: vi.fn(), retryTask: vi.fn() })
}));
vi.mock("@/components/layout/top-bar", () => ({ TopBar: () => <div data-testid="topbar" /> }));
// LANDING-CONTACT-UI-0001：横幅有专门测试（contact-flow.test.tsx），page 级与 TopBar 同款 mock 掉。
vi.mock("@/components/contact/welcome-contact-banner", () => ({ WelcomeContactBanner: () => null }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
vi.mock("@/components/tasks/task-list", () => ({ TaskList: () => <div data-testid="tasklist" /> }));
vi.mock("@/components/tasks/generation-history", () => ({ GenerationHistory: () => <div data-testid="history" /> }));
vi.mock("@/lib/api/hooks", () => ({
  useRewriteCopy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useGenerateTitles: () => ({ mutateAsync: vi.fn().mockResolvedValue({ titles: [] }), isPending: false }),
  useGenerateTopics: () => ({ mutateAsync: vi.fn().mockResolvedValue({ topics: [] }), isPending: false }),
  useSaveCopyDraft: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScriptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadVideoGenReference: () => ({ mutateAsync: vi.fn(), isPending: false }), // V2V 参考视频上传（VIDEO-GEN-V2V-UI-0001）
  useUploadProductImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAudio: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScenePromptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useBgmLibrary: () => ({ data: [], isLoading: false }),
  useVoices: () => ({
    data: [{ id: "v1", provider: "edge", voice_code: "x", display_name: "音色1", gender: null, language: null }]
  }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({ data: [] }),
  useCutoutImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCutoutBatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelBatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useModelStyles: () => ({ data: [], isLoading: false }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: { estimated_credits: 8, unit: "credits" } })
}));

import Home from "./page";

afterEach(() => vi.clearAllMocks());

const panel = (mode: string) => within(screen.getByTestId(`panel-${mode}`));
const chip = (name: string) => screen.getByRole("button", { name });
/** 切走到另一个 mode 再切回来 —— 承重测试的核心动作。 */
const leaveAndReturn = (backTo: string) => {
  fireEvent.click(chip(copy.workbench.modeCopywriting));
  fireEvent.click(chip(backTo));
};

describe("WORKBENCH-KEEPALIVE-UI-0001 · 切 tab 不丢输入（承重）", () => {
  it("数字人口播：主题 + AI 文案 → 切走 → 切回 → 原样还在", () => {
    render(<Home />);
    fireEvent.change(panel("avatar_talk").getByPlaceholderText(/输入一句话主题/), { target: { value: "保温杯种草" } });
    fireEvent.change(panel("avatar_talk").getByLabelText(copy.workbench.scriptLabel), {
      target: { value: "大家好，今天安利这款保温杯" }
    });

    leaveAndReturn(copy.workbench.modeAvatar);

    expect(panel("avatar_talk").getByPlaceholderText(/输入一句话主题/)).toHaveValue("保温杯种草");
    expect(panel("avatar_talk").getByDisplayValue("大家好，今天安利这款保温杯")).toBeInTheDocument();
  });

  it("图片生成 / 修改：提示词 → 切走 → 切回 → 原样还在", () => {
    render(<Home />);
    fireEvent.click(chip(copy.workbench.modePhoto));
    fireEvent.change(panel("photo").getByPlaceholderText(/描述想要的图片/), { target: { value: "白色大理石台面上的香水瓶" } });

    leaveAndReturn(copy.workbench.modePhoto);

    expect(panel("photo").getByPlaceholderText(/描述想要的图片/)).toHaveValue("白色大理石台面上的香水瓶");
  });

  it("视频生成：提示词 → 切走 → 切回 → 原样还在", () => {
    render(<Home />);
    fireEvent.click(chip(copy.workbench.modeVideoGen));
    fireEvent.change(panel("video_gen").getByPlaceholderText(copy.workbench.vgPromptPlaceholder), {
      target: { value: "保温杯在晨光里冒热气，镜头缓慢推近" }
    });

    leaveAndReturn(copy.workbench.modeVideoGen);

    expect(panel("video_gen").getByPlaceholderText(copy.workbench.vgPromptPlaceholder)).toHaveValue(
      "保温杯在晨光里冒热气，镜头缓慢推近"
    );
  });

  it("电商带货：产品卖点 + 画面提示词 → 切走 → 切回 → 原样还在", () => {
    render(<Home />);
    fireEvent.click(chip(copy.workbench.modeEcom));
    fireEvent.change(panel("seedance_i2v").getByPlaceholderText(/输入产品卖点/), { target: { value: "316 不锈钢，24 小时锁温" } });

    leaveAndReturn(copy.workbench.modeEcom);

    expect(panel("seedance_i2v").getByPlaceholderText(/输入产品卖点/)).toHaveValue("316 不锈钢，24 小时锁温");
  });

  it("电商图：子工具选择（AI 模特）+ 自定义补充 → 切走 → 切回 → 原样还在", () => {
    render(<Home />);
    fireEvent.click(chip(copy.workbench.modeEcomImage));
    // 切到 AI 模特子工具（子工具选择本身也是 state，切 mode 后同样要保住）。
    fireEvent.click(panel("ecom_image").getByText(copy.workbench.ecomSubToolModel));
    const custom = panel("ecom_image").getByPlaceholderText(copy.workbench.ecomCustomPlaceholder);
    fireEvent.change(custom, { target: { value: "工作室柔光、简洁白底" } });

    leaveAndReturn(copy.workbench.modeEcomImage);

    expect(panel("ecom_image").getByDisplayValue("工作室柔光、简洁白底")).toBeInTheDocument();
  });

  it("多 mode 并存：各自的输入互不串台，且切换后全部保留", () => {
    render(<Home />);
    fireEvent.change(panel("avatar_talk").getByPlaceholderText(/输入一句话主题/), { target: { value: "口播主题" } });

    fireEvent.click(chip(copy.workbench.modePhoto));
    fireEvent.change(panel("photo").getByPlaceholderText(/描述想要的图片/), { target: { value: "图片提示词" } });

    fireEvent.click(chip(copy.workbench.modeVideoGen));
    fireEvent.change(panel("video_gen").getByPlaceholderText(copy.workbench.vgPromptPlaceholder), { target: { value: "视频提示词" } });

    // 三个面板都还挂着，各自的值互不污染。
    expect(panel("avatar_talk").getByPlaceholderText(/输入一句话主题/)).toHaveValue("口播主题");
    expect(panel("photo").getByPlaceholderText(/描述想要的图片/)).toHaveValue("图片提示词");
    expect(panel("video_gen").getByPlaceholderText(copy.workbench.vgPromptPlaceholder)).toHaveValue("视频提示词");
    // 只有当前 mode 可见。
    expect(screen.getByTestId("panel-video_gen")).toBeVisible();
    expect(screen.getByTestId("panel-avatar_talk")).not.toBeVisible();
    expect(screen.getByTestId("panel-photo")).not.toBeVisible();
  });
});
