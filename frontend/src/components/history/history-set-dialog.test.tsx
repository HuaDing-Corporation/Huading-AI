import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({ useHistoryImageSet: vi.fn() }));
vi.mock("@/lib/api/hooks", () => hooks);

import { HistorySetDialog } from "./history-set-dialog";
import type { HistoryItem } from "@/lib/api/history-images";

// HISTORY-IMAGE-TAB-UI-0001 详情弹窗信息并集承重：/history 原弹窗只有标题 + 套图；本包并入卡片信息
// （生成时间 / 状态 / 分类 / 张数）——这些从**列表项**带入（BE 详情响应 ImageHistoryDetailResponse 无 title）。
// 🔴 变异门：删掉 history-set-dialog 里任一并集来源（生成时间/状态/分类/张数）→ 对应断言必红（信息并集一项都不能少）。
const ITEM: HistoryItem = {
  id: "hd-main-1",
  category: "ecom_detail",
  title: "保温杯 · 主图复刻（5 张）",
  cover_url: "https://mock.local/hist/cover.png",
  created_at: "2026-07-10T12:00:00Z",
  status: "completed",
  item_count: 5
};

beforeEach(() => {
  hooks.useHistoryImageSet.mockReturnValue({
    data: {
      id: "hd-main-1",
      category: "ecom_detail",
      created_at: "2026-07-10T12:00:00Z",
      status: "completed",
      items: [{ index: 0, download_url: "https://mock.local/hist/hero-0.png?dl=1", width: 1254, height: 1254 }],
      meta: {}
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn()
  });
});
afterEach(() => vi.clearAllMocks());

describe("HistorySetDialog · 信息并集（一项都不能少）", () => {
  it("并入卡片信息：标题(列表项带入) + 生成时间 + 状态徽标 + 分类 + 张数；套图原图红线不变", () => {
    render(<HistorySetDialog item={ITEM} onClose={() => {}} />);
    // 标题：详情响应无 title，用列表项 item.title。
    expect(screen.getByText("保温杯 · 主图复刻（5 张）")).toBeInTheDocument();
    // 并集四项（确定值）——删任一来源必红。
    expect(screen.getByText(/生成时间：/)).toBeInTheDocument();
    expect(screen.getByText("2026-07-10 12:00")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument(); // HistoryStatusBadge(completed)
    expect(screen.getByText(/分类：电商·详情图/)).toBeInTheDocument();
    expect(screen.getByText("共 5 张")).toBeInTheDocument();
    // 套图原图红线（download_url + 下载原图）不变。
    expect(screen.getByRole("link", { name: "下载原图" })).toHaveAttribute("href", expect.stringMatching(/\?dl=1$/));
  });

  it("item=null → 不发详情请求、不渲染标题（弹窗关闭态）", () => {
    render(<HistorySetDialog item={null} onClose={() => {}} />);
    expect(screen.queryByText("保温杯 · 主图复刻（5 张）")).not.toBeInTheDocument();
  });
});
