import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { VideoListItem } from "@/lib/api/types";

// HISTORY-VIDEO-DIALOG-UI-0001 · 三视频 tab 升级承重（点内容 → 大屏播放；「查看详情」→ 详情弹窗）。
// 每条钉「这次改动」本身：大屏 overlay 存在/不 autoplay/**关闭即卸载**、详情弹窗信息并集、
// 「打开详情页」把跳转能力接回来（升级前是卡片直接跳）。期望值手写，不 import 被测代码。
const pushMock = vi.hoisted(() => ({ fn: vi.fn() }));
const historyMock = vi.hoisted(() => ({ fn: vi.fn() }));
/** 共享 refetch —— presign 失效重取的落点；FIX1 的承重要数它被调了几次。 */
const refetchMock = vi.hoisted(() => ({ fn: vi.fn() }));
const deleteMock = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false, variables: undefined as string | undefined }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock.fn }) }));
vi.mock("@/lib/api/hooks", () => ({
  useVideoHistory: () => historyMock.fn(),
  useDeleteVideo: () => deleteMock,
  useClearVideos: () => ({ mutateAsync: vi.fn(), isPending: false })
}));

import { HistoryList } from "./generation-history";

/**
 * 镜像 BE 列表项（BE 列表返回的是完整 VideoRead —— 见 VideoListItem 注释）。
 *
 * 🔴 FIX1：`satisfies VideoListItem` 不是装饰，它开启**多余属性检查** —— 字段名写错当场编译红。
 * 上一版这里是裸对象字面量 + `listOf(...items: Record<string, unknown>[])`，两层一叠让类型检查形同虚设：
 * 任何形状都进得来。那个 duration_sec 的坑（BE 其实给 duration_ms）就是这么漏到运行时、只能靠确定值断言
 * 抓的 —— **类型层本该先抓到它**。实测：把下面的 duration_ms 改成 duration_sec →
 * `error TS2561: 'duration_sec' does not exist in type 'VideoListItem'. Did you mean to write 'duration_ms'?`
 */
const ITEM = {
  id: "v-1",
  status: "done",
  progress: 100,
  topic: "保温杯带货",
  created_at: "2026-07-16T12:00:00.000Z",
  playback_url: "https://cdn/v-1.mp4",
  download_url: "https://cdn/v-1.mp4?dl=1",
  thumbnail_url: "https://cdn/v-1.jpg",
  duration_ms: 18000,
  apply_visible_label: true
} satisfies VideoListItem;
const listOf = (...items: VideoListItem[]) => ({
  data: { pages: [{ items }] },
  isLoading: false,
  isError: false,
  hasNextPage: false,
  fetchNextPage: vi.fn(),
  isFetchingNextPage: false,
  refetch: refetchMock.fn
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

// ── HISTORY-FULL-PROMPT-UI-0001：详情弹窗里能看到完整提示词 + 复制 ──────────────
// 用户实测：「历史生成中要能看到完整提示词，目前只能看到短提示词，提示词长就会被截断」。
// 🔴 **三个视频 tab 的提示词是三个不同的字段**（逐个读 BE 源码确认，任务包那张表有四行是错的）：
//   video_gen   → topic（BE 创建时 self.topic = prompt，schemas/videos.py:236）
//   avatar_talk → script（topic 只是主题；用户要拿回去复用的是要念的文案）
//   seedance_i2v→ scene_prompt，**BE 未暴露**（只在 task.params）→ 本包不做，已单独报包
const LONG_PROMPT = "将图片背景换成浅蓝色带有线条波纹浅反光的纯净水，然后再将图片中的字体切换成蓝金风格。".repeat(8);

describe("三视频 tab · 详情弹窗的完整提示词（FULL-PROMPT）", () => {
  it("🔴 video_gen：提示词 = topic（BE 把 prompt 写回 topic）→ 长提示词**全文可见**，标题仍截断", () => {
    historyMock.fn.mockReturnValue(listOf({ ...ITEM, topic: LONG_PROMPT }));
    render(<HistoryList mode="video_gen" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    const dialog = screen.getByRole("dialog");

    // 🔴 **「标题与提示词同源」的处置**（任务包 §三.1 让我定 + 给理由）：
    // video_gen 的标题和提示词是**同一个字段**（BE 把 prompt 写回 topic）→ 同一句话在弹窗里出现两次。
    // 决定：**两者都保留**，本条把这个决定钉死（写 2 而不是「至少 1」——若哪天有人偷偷去掉一个，本条会红）。
    // 理由：① 标题是**索引**（一行、可扫、hover 全文），提示词块是**内容**（全文、可复制）——
    //         同源时标题就是内容的截断预览，这是「主题行 + 正文」的标准形态，不是重复；
    //       ② 改标题会破 #185 刚定稿的行为（本文件上面那条就钉着「标题 = topic」）→ §四.4 零回归；
    //       ③ 真正让它「像 bug」的不是出现两次，是**没有身份** —— 提示词块带标签 + 复制按钮，
    //         它明确是「可复制的全文」，不是标题的复读。
    const both = within(dialog).getAllByText(LONG_PROMPT);
    expect(both).toHaveLength(2); // 标题（截断预览）+ 提示词块（全文）

    // 标题仍一行（用户拍板「标题保持一行」：几百字的提示词不该把头部撑爆、把图片挤下去）
    expect(within(dialog).getByRole("heading")).toHaveClass("truncate");

    // 提示词块里的那份是**全文、不截断**（用户报的正是「长了就看不全」）
    const block = both.find((el) => el.tagName === "P");
    expect(block?.className).toContain("whitespace-pre-wrap");
    expect(block?.className).not.toContain("truncate");
  });

  it("🔴 avatar_talk：提示词 = script（不是 topic）→ 显示口播文案，标签是「口播文案」", () => {
    historyMock.fn.mockReturnValue(listOf({ ...ITEM, topic: "保温杯选题", script: "大家好，今天聊聊保温杯……" }));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("大家好，今天聊聊保温杯……")).toBeInTheDocument();
    expect(within(dialog).getByText(copy.history.scriptLabel)).toBeInTheDocument();
    // 🔴 拿 topic 冒充提示词 → 本条必红（topic 是主题，不是用户要复用的文案）
    expect(within(dialog).queryByText(copy.history.promptLabel)).not.toBeInTheDocument();
  });

  it("🔴 seedance_i2v：BE 未暴露 scene_prompt → **不显示提示词块**（宁可没有，也不拿 topic 冒充）", () => {
    historyMock.fn.mockReturnValue(listOf({ ...ITEM, topic: "保温杯卖点" }));
    render(<HistoryList mode="seedance_i2v" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText(copy.history.promptLabel)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(copy.history.scriptLabel)).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: copy.common.copy })).not.toBeInTheDocument();
  });

  it("复制按钮：点击 → 剪贴板收到**完整**提示词（不是标题那个截断版）", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true, writable: true });
    historyMock.fn.mockReturnValue(listOf({ ...ITEM, topic: LONG_PROMPT }));
    render(<HistoryList mode="video_gen" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: copy.common.copy }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LONG_PROMPT));
  });
});

// ── FIX1 · P1-1 / P1-2：新入口没继承既有防线 ───────────────────────────────────
describe("三视频 tab · 新播放器继承既有防线（FIX1）", () => {
  // 🔴 P1-2：缺 playsInline → iPhone Safari 点播放即把视频接管进**系统全屏播放器**。
  // 而「overlay 内内联播放」正是本组件存在的**全部理由**（放大静止封面零信息增量）——
  // 在移动 Safari 上没有 playsInline，这个组件等于白写。它不是锦上添花，是成立条件。
  // 注：React 把 playsInline 渲染成 DOM 属性 playsinline（全小写）。
  it("大屏 overlay 的 <video> 带 playsInline（移动 Safari 上「内联播放」的成立条件）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));
    expect(within(screen.getByRole("dialog")).getByLabelText("保温杯带货")).toHaveAttribute("playsinline");
  });

  it("详情弹窗的 <video> 带 playsInline（否则系统播放器盖住整个信息并集）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));
    expect(within(screen.getByRole("dialog")).getByLabelText("保温杯带货")).toHaveAttribute("playsinline");
  });

  // 🔴 P1-1：页面开着超过 presign TTL 再打开大屏 → 旧 URL 播不了。既有卡片播放器有这条防线
  // （onUrlError → refetch），两个新播放器没继承 → 黑屏且无任何反馈，用户不知道为什么。
  it("overlay 里 presign 失效 → 重取一次；同一 URL 连报多次也只重取一次（不打爆后端）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));

    const video = within(screen.getByRole("dialog")).getByLabelText("保温杯带货");
    fireEvent.error(video);
    expect(refetchMock.fn).toHaveBeenCalledTimes(1);

    // 浏览器对同一 src 可能连发多个 error → 哨兵必须挡住
    fireEvent.error(video);
    fireEvent.error(video);
    expect(refetchMock.fn).toHaveBeenCalledTimes(1);
  });

  it("详情弹窗里 presign 失效 → 同样重取一次（两个新入口都要有网，不是只补一个）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.tasks.open }));

    fireEvent.error(within(screen.getByRole("dialog")).getByLabelText("保温杯带货"));
    expect(refetchMock.fn).toHaveBeenCalledTimes(1);
  });

  // 🔴 硬门核心：**旧 URL 失败 → 换成新 URL**。
  // 这条钉的是「只存 id、从最新 query 派生」这个改动本身 —— 改回存任务快照 → 必红：
  // 快照冻结在点击那一刻，refetch 拿回的新 URL 永远进不到弹窗里，重取做了也白做。
  it("重取拿回新 URL → overlay 里的 <video src> 真的跟着换（存 id 派生，不是存快照）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    const { rerender } = render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));

    const before = within(screen.getByRole("dialog")).getByLabelText("保温杯带货");
    expect(before).toHaveAttribute("src", "https://cdn/v-1.mp4");
    fireEvent.error(before);
    expect(refetchMock.fn).toHaveBeenCalledTimes(1);

    // 重取回来一个新签名的 URL（弹窗仍开着）
    historyMock.fn.mockReturnValue(listOf({ ...ITEM, playback_url: "https://cdn/v-1.mp4?sig=fresh" }));
    rerender(<HistoryList mode="avatar_talk" />);

    expect(within(screen.getByRole("dialog")).getByLabelText("保温杯带货")).toHaveAttribute(
      "src",
      "https://cdn/v-1.mp4?sig=fresh"
    );
  });

  // 🔴 硬门核心：**无死循环**。
  // 「对象已被删除」时 BE 每次都签得出新 URL、但个个 404 →「URL 变了就再报一次」会变成
  // error → 重取 → 新 URL → error → …… 每轮真打一次后端。故连续失败必须封顶。
  it("新 URL 仍失效 → 连续重取封顶，不无限循环（对象已删时 BE 能一直签出新 URL）", () => {
    historyMock.fn.mockReturnValue(listOf(ITEM));
    const { rerender } = render(<HistoryList mode="avatar_talk" />);
    fireEvent.click(screen.getByRole("button", { name: copy.history.videoPlay }));

    for (let i = 1; i <= 8; i++) {
      historyMock.fn.mockReturnValue(listOf({ ...ITEM, playback_url: `https://cdn/gone.mp4?sig=${i}` }));
      rerender(<HistoryList mode="avatar_talk" />);
      fireEvent.error(within(screen.getByRole("dialog")).getByLabelText("保温杯带货"));
    }

    // 救得回来的一次就够；救不回来的最多浪费 2 次 —— 而不是 8 次、80 次。
    expect(refetchMock.fn).toHaveBeenCalledTimes(2);
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
