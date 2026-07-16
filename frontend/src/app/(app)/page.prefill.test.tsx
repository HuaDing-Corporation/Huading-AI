import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// page 级「用此文案」串联 prefill 回归：以真实 page + 真实三表单
// (CopywritingForm/NewVideoForm/EcomVideoForm) 跑完整数据流，占位无关重组件，
// mock hooks 层（不真调后端）。验证：只注入 script、topic 留空/不动、消费后不重复注入。
//
// WORKBENCH-KEEPALIVE-UI-0001 改造后的覆盖边界：
// - 面板改为「惰性挂载 + 挂载后常驻」：切 mode 不再卸载表单 → DOM 里同时存在多份表单（隐藏的那些带 hidden）。
//   故所有断言必须用 within(panel-<mode>) 限定作用域，不能再依赖「单表单挂载」这个已被废除的不变量
//   （旧断言如「切走后源文案框 not.toBeInTheDocument()」测的正是本包要消灭的 remount 副作用）。
// - 旧文件头预告的「若未来改为保留表单实例，须补『先填 topic → 串联 → script 注入但 topic 不动』的正向用例」
//   已补齐（见下方同名用例）——它同时是「prefill 只写自己带来的字段」这一契约的判决性证据。
// - page.tsx 三元的 `pendingPrefill?.target === <本表单>` 是「未消费时」的防御副防线；主防线是目标表单 effect
//   同步 props 后 clearPrefill → props 回落 undefined → early-return。

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

// 常驻后多份表单同存于 DOM → 一律按面板取作用域。
const panel = (mode: string) => within(screen.getByTestId(`panel-${mode}`));
const avatarPanel = () => panel("avatar_talk");
const ecomPanel = () => panel("seedance_i2v");
const copyPanel = () => panel("copywriting");

// 切到文案模式 → 粘贴源文案 → 生成 → 等结果区出改写文案。
async function generateCopy() {
  fireEvent.click(screen.getByRole("button", { name: "文案仿写" }));
  fireEvent.change(copyPanel().getByPlaceholderText(SOURCE), { target: { value: "原始参考文案" } });
  fireEvent.click(copyPanel().getByRole("button", { name: /生成文案/ }));
  expect(await copyPanel().findByDisplayValue("改写后的文案")).toBeInTheDocument();
}

describe("Workbench 串联 prefill 回归 (page 级)", () => {
  it("用此文案 → 数字人口播：只注入 script、topic 留空", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 数字人口播" }));

    // script 注入到口播 AI 文案框（ScriptReview → video-script）。
    expect(await avatarPanel().findByDisplayValue("改写后的文案")).toBeInTheDocument();
    // topic 留空（靠现有校验引导补全，不臆造数据）。
    expect(avatarPanel().getByPlaceholderText(AVATAR_TOPIC)).toHaveValue("");
    // 确实切到了口播：口播面板可见、文案面板转隐藏（KEEPALIVE：仍挂载但不可见，取代旧的「卸载」）。
    expect(screen.getByTestId("panel-avatar_talk")).toBeVisible();
    expect(screen.getByTestId("panel-copywriting")).not.toBeVisible();
  });

  it("用此文案 → 电商带货：script 注入电商口播文案框、卖点留空", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 电商带货" }));

    expect(await ecomPanel().findByDisplayValue("改写后的文案")).toBeInTheDocument();
    expect(ecomPanel().getByPlaceholderText(ECOM_TOPIC)).toHaveValue("");
    expect(screen.getByTestId("panel-seedance_i2v")).toBeVisible();
    expect(screen.getByTestId("panel-copywriting")).not.toBeVisible();
  });

  // 🔴 旧文件头预告的正向用例（保留表单实例后必须补）：prefill 只写自己带来的字段，用户已填的其它输入不动。
  // 这条同时否掉了「给目标表单换 key 强制重挂」那条路 —— 那样做会把先填的 topic 一起清空。
  it("prefill 只写 script、不动既有 topic（表单实例保留）", async () => {
    render(<Home />);
    // 先在口播填好 topic —— 这正是 KEEPALIVE 要保住的东西。
    fireEvent.change(avatarPanel().getByPlaceholderText(AVATAR_TOPIC), { target: { value: "保温杯种草" } });

    await generateCopy();
    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 数字人口播" }));

    expect(await avatarPanel().findByDisplayValue("改写后的文案")).toBeInTheDocument();
    // 先前填的 topic 原样保留（prefill 未带 topic → 不写该字段）。
    expect(avatarPanel().getByPlaceholderText(AVATAR_TOPIC)).toHaveValue("保温杯种草");
  });

  it("prefill 只落目标表单：带入口播后，电商表单不带残留文案", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 数字人口播" }));
    expect(await avatarPanel().findByDisplayValue("改写后的文案")).toBeInTheDocument();

    // 切到电商：电商表单不带 avatar 的残留（口播消费即 clearPrefill → 缓冲已空）。
    fireEvent.click(screen.getByRole("button", { name: "电商带货" }));
    expect(ecomPanel().queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();
    expect(ecomPanel().getByPlaceholderText(ECOM_TOPIC)).toHaveValue("");
  });

  it("不重复注入：口播注入后手改 → 切走再切回 → 保留手改值，不被旧 prefill 覆盖", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 数字人口播" }));
    const script = await avatarPanel().findByDisplayValue("改写后的文案");

    // 用户在注入值基础上手改。
    fireEvent.change(script, { target: { value: "我手改过的文案" } });

    // 来回切 tab：旧实现 remount 会清空；现在常驻保活，且 clearPrefill 后 props 已回落 undefined → 不重注入。
    fireEvent.click(screen.getByRole("button", { name: "电商带货" }));
    fireEvent.click(screen.getByRole("button", { name: "数字人口播" }));

    expect(avatarPanel().getByDisplayValue("我手改过的文案")).toBeInTheDocument();
    expect(avatarPanel().queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();
  });

  it("不重复注入(反向·电商侧)：电商注入后手改 → 切走再切回 → 保留手改值", async () => {
    render(<Home />);
    await generateCopy();

    fireEvent.click(screen.getByRole("button", { name: "用此文案 · 电商带货" }));
    const script = await ecomPanel().findByDisplayValue("改写后的文案");
    fireEvent.change(script, { target: { value: "电商手改文案" } });

    fireEvent.click(screen.getByRole("button", { name: "数字人口播" }));
    fireEvent.click(screen.getByRole("button", { name: "电商带货" }));

    expect(ecomPanel().getByDisplayValue("电商手改文案")).toBeInTheDocument();
    expect(ecomPanel().queryByDisplayValue("改写后的文案")).not.toBeInTheDocument();
  });
});
