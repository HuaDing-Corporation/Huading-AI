import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// ── HISTORY-FULL-PROMPT-UI-0001 · FIX1：图片历史的提示词接线 ─────────────────────
//
// 🔴 这块网**上一版根本不存在**，而它正是用户报障截图里的那个界面。
// Codex B 实测：删掉整行 `prompt={set?.meta?.prompt ?? set?.meta?.extra_prompt ?? null}` → 26/26 仍绿。
// 根因就在上面那个 fixture：`meta: {}` —— **fixture 里没有的东西，测试永远测不到**。
// （我上一版变异了三处、漏了这第四处 —— 而且是凭记忆挑的地方去变异，那是清单不是机制。）
//
// 各分类的提示词来源逐个读 BE 源码确认（services/image_history.py）：
//   image_gen   → meta.prompt（:426，= task.topic，与标题同源）
//   ecom_model  → meta.extra_prompt（:444 的 else 分支 —— PhotoHistoryCategory 只有四值
//                 ["image_gen","ecom_white","ecom_model","cover"]，故 else **只覆盖 ecom_model**）
//   ecom_white  → meta 只有 background / source_asset_id（:431）→ 无提示词
//   cover       → meta 只有 source / timestamp_sec 等（:435）→ 无提示词
//   ecom_detail → **走另一个 builder**（:545-559），meta 是 output_mode / product_info / selling_points…
//                 → 无 extra_prompt、无 prompt
// 期望值手写，不 import 被测代码的常量。
const setOf = (category: string, meta: Record<string, unknown>) => ({
  data: {
    id: "hd-1",
    category,
    created_at: "2026-07-10T12:00:00Z",
    status: "completed",
    items: [{ index: 0, download_url: "https://mock.local/x.png?dl=1", width: 1254, height: 1254 }],
    meta
  },
  isLoading: false,
  isError: false,
  refetch: vi.fn()
});
const itemOf = (category: string): HistoryItem => ({ ...ITEM, category: category as HistoryItem["category"] });
const LONG = "将图片背景换成浅蓝色带有线条波纹浅反光的纯净水，然后再将图片中的字体切换成蓝金风格。".repeat(6);

describe("HistorySetDialog · 完整提示词（FULL-PROMPT · FIX1 补网）", () => {
  it("🔴 image_gen → meta.prompt：长提示词全文展示 + 可复制（用户报障的正是这个界面）", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true, writable: true });
    hooks.useHistoryImageSet.mockReturnValue(setOf("image_gen", { prompt: LONG, image_size: "1024x1024" }));
    render(<HistorySetDialog item={itemOf("image_gen")} onClose={() => {}} />);

    const p = screen.getByText(LONG); // 全文在 DOM 里，不是 "…"
    expect(p).toBeInTheDocument();
    expect(p.className).toContain("whitespace-pre-wrap");
    expect(p.className).not.toContain("truncate");

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LONG)); // 剪贴板里真的是原文
  });

  it("🔴 ecom_model → meta.extra_prompt（**不是** meta.prompt —— 交换字段本条必红）", () => {
    hooks.useHistoryImageSet.mockReturnValue(
      setOf("ecom_model", { extra_prompt: "工作室柔光，白色背景", style_id: "studio", gender: "female" })
    );
    render(<HistorySetDialog item={itemOf("ecom_model")} onClose={() => {}} />);
    expect(screen.getByText("工作室柔光，白色背景")).toBeInTheDocument();
  });

  it("🔴 ecom_white → 本就没有提示词（用户只传图 + 选背景）→ 整块不渲染，不给一个空框", () => {
    hooks.useHistoryImageSet.mockReturnValue(setOf("ecom_white", { background: "transparent", source_asset_id: "a-1" }));
    render(<HistorySetDialog item={itemOf("ecom_white")} onClose={() => {}} />);
    expect(screen.queryByText("提示词")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制" })).not.toBeInTheDocument();
  });

  it("🔴 cover → 无提示词（用户只选帧 + 选模板）→ 不渲染", () => {
    hooks.useHistoryImageSet.mockReturnValue(setOf("cover", { source: "video", timestamp_sec: 3, layout_template_id: "t1" }));
    render(<HistorySetDialog item={itemOf("cover")} onClose={() => {}} />);
    expect(screen.queryByText("提示词")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制" })).not.toBeInTheDocument();
  });

  // 🔴 ecom_detail 走的是**另一个 builder**（image_history.py:545-559）—— 它的 meta 里既没有 prompt
  // 也没有 extra_prompt。我上一版的注释写着「详情 → extra_prompt」，是**假路标**（FIX1 已收准）。
  it("🔴 ecom_detail → meta 里既无 prompt 也无 extra_prompt（独立 builder）→ 不渲染", () => {
    hooks.useHistoryImageSet.mockReturnValue(
      setOf("ecom_detail", {
        output_mode: "main",
        requested_size: "1254x1254",
        product_info: { name: "保温杯" },
        selling_points: ["24 小时保温"]
      })
    );
    render(<HistorySetDialog item={itemOf("ecom_detail")} onClose={() => {}} />);
    expect(screen.queryByText("提示词")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制" })).not.toBeInTheDocument();
  });
});

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
