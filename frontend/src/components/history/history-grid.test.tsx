import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

const hooks = vi.hoisted(() => ({ useHistoryImages: vi.fn(), useHistoryImageSet: vi.fn() }));
vi.mock("@/lib/api/hooks", () => hooks);

import { HistoryGrid } from "./history-grid";

beforeEach(() => {
  hooks.useHistoryImageSet.mockReturnValue({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() });
});
afterEach(() => vi.clearAllMocks());

describe("HistoryGrid (加载/错误/重试)", () => {
  it("加载中 → 显示加载态", () => {
    hooks.useHistoryImages.mockReturnValue({ isLoading: true, isError: false, data: undefined });
    render(<HistoryGrid category="image_gen" />);
    expect(screen.getByText(copy.historyImages.loading)).toBeInTheDocument();
  });

  it("错误 → 友好中文 + 重试触发 refetch（不泄裸串）", () => {
    const refetch = vi.fn();
    hooks.useHistoryImages.mockReturnValue({ isLoading: false, isError: true, error: new Error("boom raw"), data: undefined, refetch });
    render(<HistoryGrid category="image_gen" />);
    expect(screen.queryByText(/boom raw/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: copy.historyImages.retry }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **第 4 片：先导电**（本片不挂 onError）───────────────────
//
// 图片 tab 的 `cover_url` 是 presign（history-images.ts:19），而 history-card / image-lightbox
// **全链路零 onError** → 页面开久了图片历史全挂、无恢复路径。下一片接共享哨兵。
//
// 🔴 但**顺序不能反**：直接挂 onError，弹窗那半边会得到一个「测试全绿、线上依然碎图」的防线 ——
// **比没有防线更危险**。因为 image-lightbox 的 src 来自 `useState<HistoryItem>` **冻结快照**
// （history-grid.tsx:22）：重取拿回新 URL 也喂不进弹窗，防线**不导电**。
// #185 在 generation-history 上已经踩过这个坑（P1-1），修法是**存 id、从最新 items 派生**。
//
// 本片先钉「导电」这件事本身 —— 数据源换了新 URL，UI 里的 src 必须跟着换。
// 两条一起进网是有意的：卡片那条钉的是「本来就导电，改造别弄坏它」（零回归），
// 弹窗那条钉的是「这次改动」（改前必红）。
const ITEM: HistoryItem = {
  id: "h1",
  category: "image_gen",
  title: "白色大理石上的香水瓶",
  cover_url: "https://cdn/cover-1.png",
  created_at: "2026-07-10T12:00:00Z",
  status: "completed",
  item_count: 1
};

/** 另一条：用于「删掉打开的那条、但列表非空」——不这样列表就走空态早返回，测不到派生（见下面注释）。 */
const ITEM_B: HistoryItem = { ...ITEM, id: "h2", title: "另一条", cover_url: "https://cdn/cover-2.png" };

/**
 * 跨 rerender 稳定的 refetch spy（listOf 每次调用都会重建对象，spy 不能建在里面）。
 *
 * ⚠️ **必须 mockResolvedValue**：真实 `query.refetch()` 返回 Promise，而 FIX1 的在飞门控
 * （同一 query 的并发失效只发一次）靠这个 Promise 的落定来解除。替身若返回 undefined，
 * 门控就形同虚设 → 测试测不到合流 = 假绿。**替身不真实，测试就测不到真东西。**
 */
const refetchSpy = vi.fn().mockResolvedValue(undefined);

/** 等一次重取往返落定 → 门控解除。真实链路里没有这次往返就不会有新 URL。 */
const roundTrip = () => act(async () => {});

/** infinite-query 形状（useHistoryImages 是 useInfiniteQuery，组件读 data.pages.flatMap）。 */
const listOf = (...items: HistoryItem[]) => ({
  isLoading: false,
  isError: false,
  data: { pages: [{ items, total: items.length, page: 1, page_size: 20 }] },
  hasNextPage: false,
  isFetchingNextPage: false,
  refetch: refetchSpy,
  fetchNextPage: vi.fn()
});

const FRESH = "https://cdn/cover-1.png?sig=fresh";

describe("HistoryGrid · 防线导不导电（先导电，再挂 onError）", () => {
  it("卡片封面：数据源是 items.map 派生 → 重取拿回的新 URL 喂得进去（本来就导电，别弄坏它）", () => {
    hooks.useHistoryImages.mockReturnValue(listOf(ITEM));
    const { rerender } = render(<HistoryGrid category="image_gen" />);
    expect(screen.getByAltText(ITEM.title)).toHaveAttribute("src", ITEM.cover_url);

    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: FRESH }));
    rerender(<HistoryGrid category="image_gen" />);
    expect(screen.getByAltText(ITEM.title)).toHaveAttribute("src", FRESH);
  });

  // 🔴 本片的**改动本身**：存快照 → 存 id 派生。改回 `useState<HistoryItem>` 存快照 → 必红。
  it("🔴 大图弹窗：重取拿回新 URL → 弹窗里的 <img src> 真的跟着换（存 id 派生，不是存快照）", () => {
    hooks.useHistoryImages.mockReturnValue(listOf(ITEM));
    const { rerender } = render(<HistoryGrid category="image_gen" />);
    fireEvent.click(screen.getByRole("button", { name: copy.historyImages.openLarge }));

    expect(within(screen.getByRole("dialog")).getByRole("img")).toHaveAttribute("src", ITEM.cover_url);

    // 弹窗仍开着，列表重取回来一个新签名的 URL
    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: FRESH }));
    rerender(<HistoryGrid category="image_gen" />);

    expect(within(screen.getByRole("dialog")).getByRole("img")).toHaveAttribute("src", FRESH);
  });

  // 详情弹窗的**标题/时间/状态/张数**也从列表项带入（history-set-dialog.tsx:42-59），同样会冻结。
  // 整套图本身走 HistorySetDialog 自己的 useHistoryImageSet query（不受此影响），但 meta 会。
  it("详情弹窗：列表项更新 → 弹窗 meta 跟着更新（同一处快照根因，一并拆掉）", () => {
    hooks.useHistoryImages.mockReturnValue(listOf(ITEM));
    const { rerender } = render(<HistoryGrid category="image_gen" />);
    fireEvent.click(screen.getByRole("button", { name: copy.historyImages.viewDetail }));

    expect(within(screen.getByRole("dialog")).getByText(ITEM.title)).toBeInTheDocument();

    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, title: "改了标题" }));
    rerender(<HistoryGrid category="image_gen" />);

    expect(within(screen.getByRole("dialog")).getByText("改了标题")).toBeInTheDocument();
  });

  // 派生不到（该条已被删除）→ 弹窗自动关闭，不留一个指向已消失记录的界面。
  // 与 generation-history.tsx:56-58 同口径：按**派生结果**开合，而非 id 是否存在。
  //
  // ⚠️ 这里**必须留一条 ITEM_B**：若把列表清空到 0 条，HistoryGrid 会走 `items.length === 0` 的空态
  // 早返回（history-grid.tsx:47），ImageLightbox 压根不渲染 → 弹窗当然没了，**存快照的实现也照样绿**。
  // 那样这条测试就永远不会红 = 测的是空态早返回，不是派生 —— 假绿。fixture 决定了测试能测到什么。
  it("该条被删、列表仍非空 → 弹窗自动关闭（不挂着指向已消失记录的界面）", () => {
    hooks.useHistoryImages.mockReturnValue(listOf(ITEM, ITEM_B));
    const { rerender } = render(<HistoryGrid category="image_gen" />);
    fireEvent.click(screen.getAllByRole("button", { name: copy.historyImages.openLarge })[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    hooks.useHistoryImages.mockReturnValue(listOf(ITEM_B)); // h1 没了，h2 还在 → 不走空态分支
    rerender(<HistoryGrid category="image_gen" />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **第 5 片：接防线**（导电已在第 4 片证明）────────────────
//
// 图片 tab 此前对 presign 过期**零防护** —— 不是防护弱，是连 onError 都没接：页面开久了图片历史
// 全挂成碎图，且**没有恢复路径**（用户只能刷新整页）。症状比视频轻（碎图 vs 黑屏），性质一样。
// 现在接共享哨兵：同一 URL 只报一次 / URL 变了再给一次机会 / 连续失败封顶 / 成功即清零。
describe("HistoryGrid · presign 失效 → 重取（接入共享哨兵）", () => {
  it("卡片封面失效 → 重取一次；同一 URL 连报多次也只一次（不打爆后端）", () => {
    hooks.useHistoryImages.mockReturnValue(listOf(ITEM));
    render(<HistoryGrid category="image_gen" />);

    const img = screen.getByAltText(ITEM.title);
    fireEvent.error(img);
    expect(refetchSpy).toHaveBeenCalledTimes(1);

    // 浏览器对同一 src 可能连发多个 error → 哨兵必须挡住
    fireEvent.error(img);
    fireEvent.error(img);
    expect(refetchSpy).toHaveBeenCalledTimes(1);
  });

  it("大图弹窗里失效 → 同样重取一次（两个入口都要有网，不是只补一个）", () => {
    hooks.useHistoryImages.mockReturnValue(listOf(ITEM));
    render(<HistoryGrid category="image_gen" />);
    fireEvent.click(screen.getByRole("button", { name: copy.historyImages.openLarge }));

    fireEvent.error(within(screen.getByRole("dialog")).getByRole("img"));
    expect(refetchSpy).toHaveBeenCalledTimes(1);
  });

  // 🔴 死循环刹车：图片链路原先**完全没有**这道闸 —— 裸挂 onError 就会 error→refetch→新 URL→error→……
  it("🔴 对象已删、BE 每次签出新 URL 但个个失效 → 连续重取封顶，不无限打后端", async () => {
    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: "https://cdn/gone.png?sig=0" }));
    const { rerender } = render(<HistoryGrid category="image_gen" />);

    for (let i = 1; i <= 8; i++) {
      hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: `https://cdn/gone.png?sig=${i}` }));
      rerender(<HistoryGrid category="image_gen" />);
      fireEvent.error(screen.getByAltText(ITEM.title));
      await roundTrip(); // 每轮之间隔着一次真实的重取往返，否则新 URL 根本不会出现
    }

    expect(refetchSpy).toHaveBeenCalledTimes(2);
  });

  it("加载成功即清零 → 长会话里「显示过、之后再过期」仍能再救", async () => {
    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: "https://cdn/c.png?sig=1" }));
    const { rerender } = render(<HistoryGrid category="image_gen" />);

    fireEvent.error(screen.getByAltText(ITEM.title));
    await roundTrip();
    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: "https://cdn/c.png?sig=2" }));
    rerender(<HistoryGrid category="image_gen" />);
    fireEvent.error(screen.getByAltText(ITEM.title));
    await roundTrip();
    expect(refetchSpy).toHaveBeenCalledTimes(2); // 已到封顶

    // 第 3 个 URL 真的加载出来了 → 预算清零
    hooks.useHistoryImages.mockReturnValue(listOf({ ...ITEM, cover_url: "https://cdn/c.png?sig=3" }));
    rerender(<HistoryGrid category="image_gen" />);
    fireEvent.load(screen.getByAltText(ITEM.title));

    // 很久以后它自己过期了 → 还能再救（若不清零，这里会哑 → 用户对着碎图干瞪眼）
    fireEvent.error(screen.getByAltText(ITEM.title));
    expect(refetchSpy).toHaveBeenCalledTimes(3);
  });
});
