import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// HISTORY-VIDEO-DIALOG-UI-0001 · 三视频 tab 升级承重（点内容 → 大屏播放；「查看详情」→ 详情弹窗）。
// 每条钉「这次改动」本身：大屏 overlay 存在/不 autoplay/**关闭即卸载**、详情弹窗信息并集、
// 「打开详情页」把跳转能力接回来（升级前是卡片直接跳）。期望值手写，不 import 被测代码。
const pushMock = vi.hoisted(() => ({ fn: vi.fn() }));
const historyMock = vi.hoisted(() => ({ fn: vi.fn() }));
const deleteMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false, variables: undefined as string | undefined }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock.fn }) }));
vi.mock("@/lib/api/hooks", () => ({
  useVideoHistory: () => historyMock.fn(),
  useDeleteVideo: () => deleteMock,
  useClearVideos: () => ({ mutateAsync: vi.fn(), isPending: false })
}));

import { HistoryList } from "./generation-history";

/** 镜像 BE VideoListItem 的相关字段（手写）。 */
const ITEM = {
  id: "v-1",
  status: "done",
  progress: 100,
  topic: "保温杯带货",
  created_at: "2026-07-16T12:00:00.000Z",
  playback_url: "https://cdn/v-1.mp4",
  download_url: "https://cdn/v-1.mp4?dl=1",
  thumbnail_url: "https://cdn/v-1.jpg",
  // ⚠️ BE 给的是 duration_ms，fromVideoRead 除以 1000 得 durationSec（progress-mapping.ts:120）——
  // 写成 duration_sec 会静默映射不到（时长项消失），这条是被本测试抓出来的。
  duration_ms: 18000,
  apply_visible_label: true
};
const listOf = (...items: Record<string, unknown>[]) => ({
  data: { pages: [{ items }] },
  isLoading: false,
  isError: false,
  hasNextPage: false,
  fetchNextPage: vi.fn(),
  isFetchingNextPage: false,
  refetch: vi.fn()
});

afterEach(() => vi.clearAllMocks());

describe("三视频 tab · 大屏播放 overlay", () => {
  it("点「播放视频」→ overlay 打开并内联播放（<video> 带可及名与首帧 poster）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);

    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));
    const dialog = screen.getByRole("dialog");
    const video = within(dialog).getByLabelText("保温杯带货");
    expect(video.tagName).toBe("VIDEO");
    expect(video).toHaveAttribute("src", "https://cdn/v-1.mp4");
    expect(video).toHaveAttribute("poster", "https://cdn/v-1.jpg");
    expect(video).toHaveAttribute("controls");
  });

  // 🔴 spec：历史视频含口播（有声）→ 有声 autoplay 被浏览器拦截、静音自动播对口播无意义、自动出声违反
  // 「用户发起」原则 → 只显首帧 + controls，由用户点播放。
  it("overlay 不 autoplay（有声内容由用户发起）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));
    const video = within(screen.getByRole("dialog")).getByLabelText("保温杯带货");
    expect(video).not.toHaveAttribute("autoplay");
  });

  // 🔴 硬门：留着播 = 用户切走后还在响。Radix Portal 在 open=false 时不渲染 → <video> 卸载 → 播放停止。
  it("关闭 overlay → <video> 真的从 DOM 卸载（播放随之停止）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);

    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));
    expect(within(screen.getByRole("dialog")).getByLabelText("保温杯带货")).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: copy.historyImages.close }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // 卡片上那个内联播放器不带 aria-label，故按可及名查 = 只查 overlay 里那个 → 已卸载。
    expect(screen.queryByLabelText("保温杯带货")).not.toBeInTheDocument();
  });

  it("未挂载 overlay 时不渲染任何 dialog（关闭态零残留）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("三视频 tab · 详情弹窗（信息并集 + 跳转零回归）", () => {
  it("点「查看详情」→ 详情弹窗：并入生成时间 / 状态 / 模式 / 时长 / AI 标识 + 播放器", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);

    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    const dialog = screen.getByRole("dialog");
    // 标题 = 视频主题
    expect(within(dialog).getByText("保温杯带货")).toBeInTheDocument();
    // 生成时间（卡片没显、图片详情弹窗有 → 并集补上；从列表项 created_at 带入）
    expect(within(dialog).getByText("2026-07-16 12:00")).toBeInTheDocument();
    // 「分类」对应项 = 模式中文（与 label 同处一个 span，故按正则匹配整段文本）
    expect(within(dialog).getByText(new RegExp(copy.history.tabAvatar))).toBeInTheDocument();
    // 时长（卡片有）—— 文本由多个节点拼成（时长 {n} 秒），故用正则
    expect(within(dialog).getByText(/时长\s*18\s*秒/)).toBeInTheDocument();
    // 播放器（详情弹窗内同样不 autoplay）
    const video = within(dialog).getByLabelText("保温杯带货");
    expect(video).toHaveAttribute("poster", "https://cdn/v-1.jpg");
    expect(video).not.toHaveAttribute("autoplay");
  });

  // 🔴 跳转零回归：升级前卡片「查看详情」= 直接 router.push('/videos/{id}')；升级后入口移进弹窗，能力不丢。
  it("弹窗内「打开详情页」→ router.push('/videos/{id}')（升级前的跳转能力接回来）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);

    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(pushMock.fn).not.toHaveBeenCalled(); // 「查看详情」本身不再跳页，只开弹窗

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: copy.history.videoOpenPage }));
    expect(pushMock.fn).toHaveBeenCalledTimes(1);
    expect(pushMock.fn).toHaveBeenCalledWith("/videos/v-1");
  });

  it("done 但尚无播放地址（对账中）→ 弹窗说明「仍在处理」，不给空白，且仍能打开详情页", () => {
    historyMock.fn.mockReturnValue(listOf({ ...ITEM, playback_url: null, download_url: null }));
    render(<HistoryList mode="avatar_talk" />);

    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(copy.history.videoNoPlayback)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: copy.history.videoOpenPage })).toBeInTheDocument();
  });

  it("电商 / 视频生成 tab 的模式中文各自正确（详情弹窗的「分类」项）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    const { unmount } = render(<HistoryList mode="seedance_i2v" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(within(screen.getByRole("dialog")).getByText(new RegExp(copy.history.tabEcom))).toBeInTheDocument();
    unmount();

    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="video_gen" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(within(screen.getByRole("dialog")).getByText(new RegExp(copy.history.tabVideoGen))).toBeInTheDocument();
  });
});
