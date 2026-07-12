import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// page 级「用此文案」串联 prefill 回归：以真实 page + 真实三表单
// (CopywritingForm/NewVideoForm/EcomVideoForm) 跑完整数据流，占位无关重组件，
// mock hooks 层（不真调后端）。验证：只注入 script、topic 留空、onPrefillConsumed
// 清空后口播↔电商【双向】切回不重注入旧文案。
//
// 覆盖边界说明（Code Review 记录）：
// - 「prefill 仅写 script、不动既有 topic」契约当前依赖 mode 切换 remount 目标表单
//   （切模式即全新挂载，无既有 topic 可被覆盖）；若未来改为保留表单实例（不 remount），
//   须补「先填 topic → 串联 → script 注入但 topic 不动」的正向用例。
// - page.tsx 三元的 `pendingPrefill?.target === <本表单>` 是「未消费时」的防御副防线；
//   主防线是目标表单 mount 即消费并 clearPrefill。真实流程主防线总先生效（消费即清空），
//   故副防线（排除错误 target 的注入）在 page 级不可达，由下方双向切回用例间接守住。

const rewriteMock = vi.hoisted(() => ({ mutateAsync: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ back: vi.fn(), push: vi.fn() }) }));
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: { role: "admin", user: { permissions: ["voice_clone_vip"] } }, ready: true }) }));
vi.mock("@/lib/videos/tasks-context", () => ({
  useVideoTasks: () => ({ tasks: [], createAndTrack: vi.fn(), refreshTask: vi.fn(), retryTask: vi.fn() })
}));
// 占位与 prefill 链路无关的重组件（避免其 hook 依赖；prefill 编排不经它们）。
vi.mock("@/components/layout/top-bar", () => ({ TopBar: () => <div data-testid="topbar" /> }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
vi.mock("@/components/tasks/task-list", () => ({ TaskList: () => <div data-testid="tasklist" /> }));
vi.mock("@/components/tasks/generation-history", () => ({ GenerationHistory: () => <div data-testid="history" /> }));
// mock hooks 层（仅真实三表单 + ConfirmGenerateDialog 用到的）；不真调后端。
vi.mock("@/lib/api/hooks", () => ({
  useRewriteCopy: () => ({ mutateAsync: rewriteMock.mutateAsync, isPending: false }),
  useGenerateTitles: () => ({ mutateAsync: vi.fn().mockResolvedValue({ titles: [] }), isPending: false }),
  useGenerateTopics: () => ({ mutateAsync: vi.fn().mockResolvedValue({ topics: [] }), isPending: false }),
  useSaveCopyDraft: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScriptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadAvatarVideo: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadProductImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useScenePromptGenerate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrandVoices: () => ({ data: [], isLoading: false }),
  useVoices: () => ({
    data: [{ id: "v1", provider: "edge", voice_code: "x", display_name: "音色1", gender: null, language: null }]
  }),
  useAvatarPresets: () => ({ data: [] }),
  useSubtitleTemplates: () => ({ data: [] }),
  useEstimateVideo: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, data: { estimated_credits: 8, unit: "credits" } })
}));

import Home from "./page";

beforeEach(() => {
  rewriteMock.mutateAsync.mockResolvedValue({ results: [{ text: "改写后的文案" }] });
});
afterEach(() => vi.clearAllMocks());

const SOURCE = /粘贴你有权使用/;
const AVATAR_TOPIC = /输入一句话主题/;
const ECOM_TOPIC = /输入产品卖点/;

// 切到文案模式 → 粘贴源文案 → 生成 → 等结果区出改写文案。
async function generateCopy() {
  fireEvent.click(screen.getByRole("button", { name: "文案仿写" }));
  fireEvent.change(screen.getByPlaceholderText(SOURCE), { target: { value: "原始参考文案" } });
  fireEvent.click(screen.getByRole("button", { name: /生成文案/ }));
  expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
}

describe("Workbench 串联 prefill 回归 (page 级)", () => {
  it("用此文案 → 数字人口播：只注入 script、topic 留空", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 数字人口播" }));

    // script 注入到口播 AI 文案框（ScriptReview → video-script）。
    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    // 单表单挂载不变量：copy 结果区与 video-script 互斥（findByDisplayValue 唯一性前提）。
    expect(screen.getAllByDisplayValue("改写后的文案")).toHaveLength(1);
    // topic 留空（靠现有校验引导补全，不臆造数据）。
    expect(screen.getByPlaceholderText(AVATAR_TOPIC)).toHaveValue("");
    // 已离开文案模式：源文案框不在（确认确实切到口播表单）。
    expect(screen.queryByPlaceholderText(SOURCE)).not.toBeInTheDocument();
  });

  it("用此文案 → 电商带货：script 注入电商口播文案框、卖点留空", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 电商带货" }));

    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(screen.getAllByDisplayValue("改写后的文案")).toHaveLength(1);
    expect(screen.getByPlaceholderText(ECOM_TOPIC)).toHaveValue("");
    expect(screen.queryByPlaceholderText(SOURCE)).not.toBeInTheDocument();
  });

  it("口播 ↔ 电商 切回：onPrefillConsumed 清空后不重注入旧文案", async () => {
    render(<Home />);
    await generateCopy();

    // 串联进口播 → script 注入。
    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 数字人口播" }));
    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();

    // 切到电商：电商表单不带 avatar 残留文案（avatar mount 即消费并 clearPrefill →
    // 此断言主守「clearPrefill 生效」，见 clearPrefill→no-op 变异验证）。
    fireEvent.click(screen.getByRole("button", { name: "电商带货" }));
    expect(screen.queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();

    // 切回口播：NewVideoForm 重新挂载 → pendingPrefill 已清，不重注入旧文案。
    fireEvent.click(screen.getByRole("button", { name: "数字人口播" }));
    expect(screen.queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText(AVATAR_TOPIC)).toHaveValue("");
    // 显式锁单表单挂载不变量：已离开文案模式，源文案框不在。
    expect(screen.queryByPlaceholderText(SOURCE)).not.toBeInTheDocument();
  });

  it("电商 ↔ 口播 切回(反向)：EcomVideoForm 消费清空后不重注入旧文案", async () => {
    render(<Home />);
    await generateCopy();

    // 串联进电商 → script 注入电商口播文案框（消费侧是 EcomVideoForm 那份 consumedRef/effect）。
    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 电商带货" }));
    expect(await screen.findByDisplayValue("改写后的文案")).toBeInTheDocument();

    // 切到口播：口播表单不带电商残留文案。
    fireEvent.click(screen.getByRole("button", { name: "数字人口播" }));
    expect(screen.queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();

    // 切回电商：EcomVideoForm 重新挂载 → pendingPrefill 已清，不重注入旧文案。
    fireEvent.click(screen.getByRole("button", { name: "电商带货" }));
    expect(screen.queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText(ECOM_TOPIC)).toHaveValue("");
    expect(screen.queryByPlaceholderText(SOURCE)).not.toBeInTheDocument();
  });
});
