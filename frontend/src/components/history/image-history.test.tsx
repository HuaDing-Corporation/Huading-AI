import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

// 隔离网络：mock hook 层（adapter/mock 真契约由 history-images.test.ts MSW 覆盖）。此处锁 4 tab 切换 + 跨类型不串数据
// + 空态/分页/partial 标注 + 重开整套弹窗（下载红线）。
const hooks = vi.hoisted(() => ({ useHistoryImages: vi.fn(), useHistoryImageSet: vi.fn() }));
vi.mock("@/lib/api/hooks", () => hooks);

import { ImageHistory } from "./image-history";

function card(id: string, title: string, over?: Partial<HistoryItem>): HistoryItem {
  return { id, category: "image_gen", title, cover_url: `https://cdn/${id}.png`, created_at: "2026-07-10T12:00:00Z", status: "ready", item_count: 1, ...over };
}

const SEED: Record<string, HistoryItem[]> = {
  image_gen: [card("g1", "创意图甲")],
  ecom_white: [card("w1", "白底图乙", { category: "ecom_white" })],
  ecom_model: [card("m1", "模特图丙", { category: "ecom_model", item_count: 4, status: "completed" })],
  ecom_detail: [card("d1", "详情图丁", { category: "ecom_detail", item_count: 12, status: "partial_failed" })]
};

function listResult(items: HistoryItem[], over?: Record<string, unknown>) {
  return {
    data: { pages: [{ items, total: items.length, page: 1, page_size: 20 }] },
    isLoading: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: vi.fn(),
    ...over
  };
}

beforeEach(() => {
  hooks.useHistoryImages.mockImplementation((cat: string) => listResult(SEED[cat] ?? []));
  hooks.useHistoryImageSet.mockReturnValue({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() });
});
afterEach(() => vi.clearAllMocks());

// Radix TabsTrigger 在 automatic 模式下靠 focus/mouseDown 激活（非 click）——jsdom 下需显式触发。
function selectTab(name: string) {
  const tab = screen.getByRole("tab", { name });
  fireEvent.focus(tab);
  fireEvent.mouseDown(tab);
}

describe("ImageHistory (4 tab · 归一 category)", () => {
  it("4 tab 标签齐备；默认图片生成 tab 显示本类，不串其它类", () => {
    render(<ImageHistory />);
    for (const label of [copy.historyImages.tabImageGen, copy.historyImages.tabEcomWhite, copy.historyImages.tabEcomModel, copy.historyImages.tabEcomDetail]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText("创意图甲")).toBeInTheDocument();
    expect(screen.queryByText("白底图乙")).not.toBeInTheDocument();
  });

  it("跨类型不串数据：切到「电商·白底图」→ 显示白底，隐藏图片生成项", () => {
    render(<ImageHistory />);
    selectTab(copy.historyImages.tabEcomWhite);
    expect(screen.getByText("白底图乙")).toBeInTheDocument();
    expect(screen.queryByText("创意图甲")).not.toBeInTheDocument();
  });

  it("详情图 tab：卡片显示张数(12) + partial_failed 标注", () => {
    render(<ImageHistory />);
    selectTab(copy.historyImages.tabEcomDetail);
    expect(screen.getByText("详情图丁")).toBeInTheDocument();
    expect(screen.getByText(copy.historyImages.itemCount(12))).toBeInTheDocument();
    expect(screen.getByText(copy.historyImages.statusPartial)).toBeInTheDocument();
  });

  it("空态：该分类无记录 → 友好空态引导", () => {
    hooks.useHistoryImages.mockImplementation(() => listResult([]));
    render(<ImageHistory />);
    expect(screen.getByText(copy.historyImages.empty)).toBeInTheDocument();
  });

  it("分页：hasNextPage → 「加载更多」点击触发 fetchNextPage", () => {
    const fetchNextPage = vi.fn();
    hooks.useHistoryImages.mockImplementation((cat: string) =>
      cat === "image_gen" ? listResult(SEED.image_gen, { hasNextPage: true, fetchNextPage }) : listResult(SEED[cat] ?? [])
    );
    render(<ImageHistory />);
    fireEvent.click(screen.getByRole("button", { name: copy.historyImages.loadMore }));
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("重开整套：点卡片 → 弹窗展示整套（下载原图<a download> + 原始尺寸），缺图张禁用", () => {
    hooks.useHistoryImageSet.mockReturnValue({
      data: {
        id: "g1",
        category: "image_gen",
        created_at: "2026-07-10T12:00:00Z",
        status: "completed",
        items: [
          { index: 0, download_url: "https://cdn/set-0.png?dl=1", width: 1024, height: 1024 },
          { index: 1, download_url: null, width: null, height: null }
        ]
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn()
    });
    render(<ImageHistory />);
    fireEvent.click(screen.getByRole("button", { name: /创意图甲/ }));
    // 弹窗内：第 1 张可下载原图（href=download_url + download 属性）
    const link = screen.getByRole("link", { name: copy.historyImages.download });
    expect(link).toHaveAttribute("href", "https://cdn/set-0.png?dl=1");
    expect(link).toHaveAttribute("download");
    expect(screen.getByText(copy.historyImages.sizeLabel("1024x1024"))).toBeInTheDocument();
    // 缺图张 → 禁用态、不死链
    expect(screen.getByText(copy.historyImages.downloadUnavailable)).toBeInTheDocument();
    // 红线：零 canvas
    expect(document.querySelector("canvas")).toBeNull();
    // 可见关闭控件（可达性）
    expect(screen.getByRole("button", { name: copy.historyImages.close })).toBeInTheDocument();
  });
});
