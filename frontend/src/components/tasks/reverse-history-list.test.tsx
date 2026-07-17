import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// HISTORY-VIDEO-REVERSE-UI-0001 · 反推历史承重。
// 每条都钉「这次改动」本身（#181/#182 沉淀：测试要钉改动、不是钉功能）：
//  - 二级分类切换 → source_kind 参数真的变（删掉传参 → 必红）
//  - 删除确认门 → 取消不删 / 确认恰删一次（去掉确认门 → 必红）
//  - 详情弹窗「带入」→ 以正确落点冒泡（复用既有闭环，不重造）
//  - 视频无缩略图（BE 恒 null）→ 显式占位
// mock hooks 层（不真调后端）；期望值手写，不 import 被测代码的常量。
const mocks = vi.hoisted(() => ({
  jobs: vi.fn(),
  job: vi.fn(),
  del: { mutateAsync: vi.fn(), isPending: false, variables: undefined as string | undefined },
  save: { mutateAsync: vi.fn(), isPending: false }
}));

vi.mock("@/lib/api/hooks", () => ({
  useReversePromptJobs: (kind?: string) => mocks.jobs(kind),
  useReversePromptJob: (id?: string) => mocks.job(id),
  useDeleteReversePromptJob: () => mocks.del,
  useSaveReversePrompt: () => mocks.save
}));

import { ReverseHistoryList } from "./reverse-history-list";

/** 列表项形状 = BE ReversePromptHistoryItem 的 6 字段（手写，不 import 被测代码的类型）。 */
interface Item {
  id: string;
  source_kind: "image" | "video";
  status: string;
  created_at: string;
  source_thumbnail_url: string | null;
  summary: string | null;
}

const IMG_ITEM: Item = {
  id: "rh-img-1",
  source_kind: "image",
  status: "succeeded",
  created_at: "2026-07-16T12:00:00.000Z",
  source_thumbnail_url: "https://mock.local/reverse/src-1.png",
  summary: "白色大理石台面上的便携保温杯，暖色晨光"
};
const VID_ITEM: Item = {
  id: "rh-vid-1",
  source_kind: "video",
  status: "succeeded",
  created_at: "2026-07-16T11:57:00.000Z",
  source_thumbnail_url: null, // BE 事实：video 源恒 null
  summary: "保温杯带货短片"
};
const FAILED_ITEM: Item = {
  id: "rh-img-3",
  source_kind: "image",
  status: "failed",
  created_at: "2026-07-16T11:58:00.000Z",
  source_thumbnail_url: "https://mock.local/reverse/src-3.png",
  summary: null
};

const listOf = (...items: Item[]) => ({
  data: { pages: [{ items, total: items.length, page: 1, page_size: 20 }] },
  isLoading: false,
  isError: false,
  hasNextPage: false,
  fetchNextPage: vi.fn(),
  isFetchingNextPage: false,
  refetch: vi.fn()
});
const RESULT = {
  target_format: "seedance_2_0",
  prompt_zh: "白色大理石台面上的便携保温杯",
  prompt_en: "A portable insulated bottle",
  negative_prompt: "",
  style_tags: [],
  camera: "",
  lighting: "",
  composition: "",
  subject: "",
  scene: "",
  motion_hint: "",
  selling_points: [],
  text_in_media: [],
  disclaimer: "AI 近似重建，仅供参考",
  confidence: 0.8,
  fill_targets: {
    avatar_talk: { topic: "保温杯种草", script: "大家好……" },
    seedance_i2v: { topic: "保温杯卖点", scene_prompt: "暖光特写" },
    video_gen: { topic: "保温杯", prompt: "暖光特写，环绕运镜" },
    photo: { topic: "白色大理石台面上的保温杯" },
    ecom_model: { extra_prompt: "工作室柔光" },
    ecom_poster: { title: "大促", subtitle: "限时" }
  },
  video_analysis: null
};
const jobOf = (over: Record<string, unknown> = {}) => ({
  data: {
    id: "rh-img-1",
    status: "succeeded",
    source_kind: "image",
    target_format: "seedance_2_0",
    result: RESULT,
    error_code: null,
    error_message: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    credits: 0,
    cost_cents: 0,
    created_at: "2026-07-16T12:00:00.000Z",
    updated_at: "2026-07-16T12:00:00.000Z",
    saved_at: null,
    ...over
  },
  isLoading: false,
  isError: false,
  refetch: vi.fn()
});
const noJob = { data: undefined, isLoading: false, isError: false, refetch: vi.fn() };

afterEach(() => vi.clearAllMocks());

describe("ReverseHistoryList (提示词反推历史)", () => {
  // 🔴 变异门①：把 useReversePromptJobs(kind === "all" ? undefined : kind) 的传参删掉 → 本条必红。
  it("二级分类切换 → source_kind 参数真的跟着变（全部=不传 / image / video）", () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);
    expect(mocks.jobs).toHaveBeenCalledWith(undefined); // 默认「全部」→ 省略 source_kind（BE：省略=全部）

    fireEvent.click(screen.getByRole("button", { name: copy.history.reverseKindImage }));
    expect(mocks.jobs).toHaveBeenLastCalledWith("image");

    fireEvent.click(screen.getByRole("button", { name: copy.history.reverseKindVideo }));
    expect(mocks.jobs).toHaveBeenLastCalledWith("video");

    fireEvent.click(screen.getByRole("button", { name: copy.history.reverseKindAll }));
    expect(mocks.jobs).toHaveBeenLastCalledWith(undefined);
  });

  // a11y：用户拍板「tab 内再分」→ 用 chip group，**不得**引入第二层 tablist（读屏会连播两组 tab、分不清层级）。
  it("二级分类是 tab 内的 chip group：group 有可及名、chip 用 aria-pressed、无第二层 tablist", () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);

    const group = screen.getByRole("group", { name: copy.history.reverseKindLabel });
    expect(within(group).getAllByRole("button")).toHaveLength(3);
    expect(within(group).getByRole("button", { name: copy.history.reverseKindAll })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(within(group).getByRole("button", { name: copy.history.reverseKindImage })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
    expect(screen.queryAllByRole("tablist")).toHaveLength(0);
  });

  // 🔴 BE 事实（services:320-322）：video 源恒无缩略图 → UI 必须显式占位，不能假设有图。
  it("视频反推无缩略图 → 显式占位、不渲染空 <img>", () => {
    mocks.jobs.mockReturnValue(listOf(VID_ITEM));
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);
    expect(screen.getByText(copy.history.reverseNoThumb)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    // scope 到列表：「视频反推」既是二级分类 chip、也是卡片上的来源标签，page 级查询会双命中。
    expect(within(screen.getByRole("list")).getByText("视频反推")).toBeInTheDocument();
  });

  it("图片反推有缩略图 → 渲染 <img>，摘要照显；无摘要走占位", () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM, FAILED_ITEM));
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);
    expect(document.querySelectorAll("img")).toHaveLength(2);
    expect(screen.getByText(/白色大理石台面上的便携保温杯/)).toBeInTheDocument();
    expect(screen.getByText(copy.history.reverseNoSummary)).toBeInTheDocument(); // failed 无 summary
    expect(screen.getByText("失败")).toBeInTheDocument(); // 状态如实透出
  });

  // 详情惰性（同 HistorySetDialog 惯例）：不打开就不拉详情 —— 列表页别为每条预热详情。
  it("详情惰性：未打开弹窗 → 不拉详情", () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);
    expect(mocks.job).toHaveBeenCalledWith(undefined);
    expect(mocks.job).not.toHaveBeenCalledWith("rh-img-1");
  });

  it("点「查看详情」→ 才拉该条详情并渲染反推结果", async () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(jobOf());
    render(<ReverseHistoryList />);

    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    await waitFor(() => expect(mocks.job).toHaveBeenCalledWith("rh-img-1"));
    expect(screen.getByText(copy.history.reverseDetailTitle)).toBeInTheDocument();
    // 结果块由复用的 ReversePromptResultView 渲染（CopyableBlock 文本，非 input）。
    expect(screen.getByText("白色大理石台面上的便携保温杯")).toBeInTheDocument();
    expect(screen.getByText(copy.reverse.blockPromptZh)).toBeInTheDocument();
  });

  // 🔴 变异门②：把「带入」的 onApplyPrefill 冒泡去掉 → 本条必红。复用既有闭环（fillTargetToPrefill），不重造。
  it("详情弹窗「带入 · 数字人口播」→ 以正确落点冒泡 onApplyPrefill，并关闭弹窗", async () => {
    const onApplyPrefill = vi.fn();
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(jobOf());
    render(<ReverseHistoryList onApplyPrefill={onApplyPrefill} />);

    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.applyAvatar }));

    expect(onApplyPrefill).toHaveBeenCalledTimes(1);
    expect(onApplyPrefill).toHaveBeenCalledWith({ target: "avatar_talk", topic: "保温杯种草", script: "大家好……" });
    await waitFor(() => expect(screen.queryByText(copy.history.reverseDetailTitle)).not.toBeInTheDocument());
  });

  it("带入 · 电商图(AI 模特) → 落 ecom_image/tool=model/custom（走既有 fillTargetToPrefill 映射）", async () => {
    const onApplyPrefill = vi.fn();
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(jobOf());
    render(<ReverseHistoryList onApplyPrefill={onApplyPrefill} />);

    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    fireEvent.click(await screen.findByRole("button", { name: copy.reverse.applyEcomModel }));
    expect(onApplyPrefill).toHaveBeenCalledWith({ target: "ecom_image", tool: "model", custom: "工作室柔光" });
  });

  // 🔴 防二次扣费：视频「重新反推」100 积分/次且历史场景无计费门 → 详情弹窗必须隐藏该入口。
  it("详情弹窗隐藏「重新反推」（防二次扣费），但保留「保存到历史」", async () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(jobOf());
    render(<ReverseHistoryList />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    expect(await screen.findByRole("button", { name: copy.reverse.save })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.reverse.regenerate })).not.toBeInTheDocument();
  });

  it("failed 的详情：不渲染结果块，显示友好错误（不回落裸技术串）", async () => {
    mocks.jobs.mockReturnValue(listOf(FAILED_ITEM));
    mocks.job.mockReturnValue(
      jobOf({ id: "rh-img-3", status: "failed", result: null, error_code: "REVERSE_FAILED", error_message: "图片解析失败，请换一张更清晰的图片重试" })
    );
    render(<ReverseHistoryList />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    expect(await screen.findByText("图片解析失败，请换一张更清晰的图片重试")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.reverse.applyAvatar })).not.toBeInTheDocument();
  });

  it("queued/running 的详情：无结果 → 明确提示尚未完成（不显空白）", async () => {
    mocks.jobs.mockReturnValue(listOf({ ...VID_ITEM, status: "running", summary: null }));
    mocks.job.mockReturnValue(jobOf({ id: "rh-vid-2", status: "running", result: null }));
    render(<ReverseHistoryList />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(await screen.findByText(copy.history.reversePendingHint)).toBeInTheDocument();
  });

  // 🔴 变异门③：去掉 ConfirmDialog 确认门（点删除直接 mutateAsync）→ 本条必红。
  // 🔴 FIX3 · P2 假路标：这条测试名原本写的是「文案是软删『可恢复』口径」—— 而它的断言从 FIX2 起就
  // **明确禁止**「可恢复」那句文案。**名字和断言说的是相反的话。**
  // 名字说谎比没有守卫更危险：读的人信名字、不读断言，就以为「可恢复」是被守着的口径。
  // （本项目在 quota 那边刚栽过同款：`centralized_in_locked_services` 只证明「集中」、没证明「加锁」。）
  it("删除确认门：点删除只弹确认不删 → 取消不删 → 确认恰删一次；文案只讲用户可见后果", async () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(noJob);
    mocks.del.mutateAsync.mockResolvedValue({ id: "rh-img-1", deleted_at: "2026-07-16T12:30:00.000Z" });
    render(<ReverseHistoryList />);

    fireEvent.click(screen.getByLabelText(copy.history.deleteItem));
    expect(mocks.del.mutateAsync).not.toHaveBeenCalled(); // 确认门：不直接删
    expect(screen.getByText(copy.history.reverseDeleteConfirmTitle)).toBeInTheDocument();
    // 🔴 FIX2：文案只讲用户可观察的后果 —— 列表消失 + 详情 404 + 无恢复入口 → 「将从历史移除，无法撤销。」
    // 不用「可恢复」（软删是 BE 的运维保险、不是用户功能，用户没有回收站），也不用「永久删除」（谎报实现：
    // 数据其实都在、媒体没碰）。
    expect(screen.getByText(copy.history.deleteConfirmNoUndo)).toBeInTheDocument();
    // 🔴 COPY-DRAFT-DELETE-COPY-FIX-0001：这两条反断言**写死字面量**，不再 `copy.history.deleteConfirmSoft`。
    // 三个理由：① 那个 key 已被删除（它就是那个谎），import 它编译都过不了；
    // ② 反断言的意图是「**这句话**永远不许出现在用户眼前」—— 钉字面量才拦得住「有人把同样的话换个 key 名加回来」，
    //    钉 key 只能拦住「有人用那个 key」；③ 本文件开头自己写的规矩就是「期望值手写、不 import 被测代码的常量」——
    //    原来那行其实一直在违反它。
    expect(screen.queryByText("将从历史移除（可恢复）。")).not.toBeInTheDocument();
    expect(screen.queryByText("将永久删除，不可恢复。")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));
    expect(mocks.del.mutateAsync).not.toHaveBeenCalled(); // 取消不删

    fireEvent.click(screen.getByLabelText(copy.history.deleteItem));
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));
    await waitFor(() => expect(mocks.del.mutateAsync).toHaveBeenCalledTimes(1)); // 确认恰一次
    expect(mocks.del.mutateAsync).toHaveBeenCalledWith("rh-img-1");
  });

  it("删除失败 → 弹窗内提示，且弹窗不关（可重试）", async () => {
    mocks.jobs.mockReturnValue(listOf(IMG_ITEM));
    mocks.job.mockReturnValue(noJob);
    mocks.del.mutateAsync.mockRejectedValue(new Error("boom"));
    render(<ReverseHistoryList />);

    fireEvent.click(screen.getByLabelText(copy.history.deleteItem));
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));
    // 失败原因必须在弹窗内可见（不被模态遮罩盖），且弹窗仍开可重试 —— 与 HistoryList 既有惯例一致。
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(copy.history.deleteFailed)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: copy.history.deleteConfirmBtn })).toBeInTheDocument();
  });

  it("空态：无记录 → 友好占位", () => {
    mocks.jobs.mockReturnValue(listOf());
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);
    expect(screen.getByText(copy.history.reverseEmpty)).toBeInTheDocument();
  });

  // 🔴 FIX2 · P2 补网：Codex B 指出 copy.ts `?? status` 会把 BE 的裸英文漏给用户。修之前**没有任何测试碰过
  // 这条兜底** —— 我做变异硬门时把 `?? "未知状态"` 改回 `?? status`，全量测试竟然全绿 = 修了个没网的洞。
  // 故先补这条：BE 若哪天加了新状态（DB CheckConstraint 之外的值），界面必须收敛成中文，不能露 "cancelled"。
  it("BE 出现枚举外的新状态 → 徽标显示「未知状态」，不把裸英文漏给用户", () => {
    mocks.jobs.mockReturnValue(listOf({ ...IMG_ITEM, id: "rh-img-x", status: "cancelled" }));
    mocks.job.mockReturnValue(noJob);
    render(<ReverseHistoryList />);
    expect(screen.getByText("未知状态")).toBeInTheDocument();
    expect(screen.queryByText("cancelled")).not.toBeInTheDocument();
  });
});
