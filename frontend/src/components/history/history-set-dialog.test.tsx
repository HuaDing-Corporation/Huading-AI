// rebase(#187)：两边的并集 —— #187 要 waitFor（提示词复制的异步断言），本 PR 要 fireEvent（error/load 事件）。
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
    refetch: vi.fn().mockResolvedValue(undefined)
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
  refetch: vi.fn().mockResolvedValue(undefined)
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

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · **第 5 片：接防线** ──────────────────────────────────
//
// ⚠️ **证伪任务包一处**：任务包 §二.3 点名「history-card / image-lightbox / **history-detail-dialog**
// 全链路零 onError」。但 `history-detail-dialog.tsx` 是**纯外壳**（Dialog/标题/meta/关闭），**没有 img** ——
// 详情弹窗里真正裸着的 `<img>` 在 `history-image-tile.tsx:27`，消费的是 `download_url`
// （history-images.ts:41 明确标注 presigned）。以源码为准 → 本片接的是 tile。
//
// tile 的数据源是 HistorySetDialog 自己的 `useHistoryImageSet` query（不是 history-grid 那个冻结快照），
// 故它**本来就导电**：重取 → set.items 换新 download_url → tile 的 src 跟着换。承重见下面第 3 条。
describe("HistorySetDialog · 整套图 presign 失效 → 重取（接入共享哨兵）", () => {
  it("整套单张失效 → 重取一次；同一 URL 连报多次也只一次", () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
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
      refetch
    });
    render(<HistorySetDialog item={ITEM} onClose={() => {}} />);

    const img = screen.getByRole("img");
    fireEvent.error(img);
    expect(refetch).toHaveBeenCalledTimes(1);

    fireEvent.error(img);
    fireEvent.error(img);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  // 🔴 **上一版这条测试在给错误行为盖章**（Codex B 的 P1-2）。
  // 它原本叫「每张各自独立计数 —— 一张失效不该吃掉另一张的重取机会」，断言 `refetch` 被调 **2** 次，
  // 理由写的是「一张不该吃掉另一张的机会」。**那个框架从根上就错了**：
  // refetch 刷的是**整个 set query** —— 第一次回来时第二张的 download_url 也已经换新了，
  // 第二次 refetch 是**纯重复请求**。所谓「另一张的机会」根本不存在，它们本来就是同一次机会。
  // 于是「每实例最多 2 次」在 N 张的整套上 = 最多 2×N 次真实请求；而 TanStack 默认 cancelRefetch:true
  // 让两次并发 refetch 的 queryFn 被调 **3** 次（第二次中止并重启第一次 —— 我自己跑探针复现了这个数字）。
  //
  // 这比"漏测"重一层：不是没看见，是看见了并盖章说对。改的不是数字，是**预算的作用域**。
  it("🔴 整套 N 张同时失效 → 只重取一次（一次 refetch 就把 N 张的 URL 全刷回来）", () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    hooks.useHistoryImageSet.mockReturnValue({
      data: {
        id: "hd-main-1",
        category: "ecom_detail",
        created_at: "2026-07-10T12:00:00Z",
        status: "completed",
        items: [
          { index: 0, download_url: "https://mock.local/a.png?dl=1", width: 1254, height: 1254 },
          { index: 1, download_url: "https://mock.local/b.png?dl=1", width: 1254, height: 1254 }
        ],
        meta: {}
      },
      isLoading: false,
      isError: false,
      refetch
    });
    render(<HistorySetDialog item={ITEM} onClose={() => {}} />);

    // 整套 N 张共用**一份**预算（scope 由持有 query 的 HistorySetDialog 创建并整份下发）。
    // 两张同时碎 → 第一张触发重取，第二张看到「已有一次在路上」→ 不再发。
    const imgs = screen.getAllByRole("img");
    fireEvent.error(imgs[0]);
    fireEvent.error(imgs[1]);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("重取拿回新 URL → tile 的 <img src> 真的跟着换（数据源是 query 派生，重取才有意义）", () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const setWith = (url: string) => ({
      data: {
        id: "hd-main-1",
        category: "ecom_detail",
        created_at: "2026-07-10T12:00:00Z",
        status: "completed",
        items: [{ index: 0, download_url: url, width: 1254, height: 1254 }],
        meta: {}
      },
      isLoading: false,
      isError: false,
      refetch
    });
    hooks.useHistoryImageSet.mockReturnValue(setWith("https://mock.local/hero-0.png?dl=1"));
    const { rerender } = render(<HistorySetDialog item={ITEM} onClose={() => {}} />);

    fireEvent.error(screen.getByRole("img"));
    hooks.useHistoryImageSet.mockReturnValue(setWith("https://mock.local/hero-0.png?dl=1&sig=fresh"));
    rerender(<HistorySetDialog item={ITEM} onClose={() => {}} />);

    expect(screen.getByRole("img")).toHaveAttribute("src", "https://mock.local/hero-0.png?dl=1&sig=fresh");
  });
});

// ── FIX5：inFlight 也按 set 隔离（整个 RefreshGroup 按 scope 分，不只 mediaKey）─────────────────
//
// 🔴 这条钉的是**现有跨 job 测试绕过的那个窗口**：跨 job 测试切 B **前先等 A settle**，只覆盖了「预算」隔离。
// 本条**故意让 A 的 refetch 保持 pending**（永不落定）就切 B —— 若 group 的 inFlight 仍由根组 "" 共享，
// B 首帧报错会被 `if (group.inFlight) return`（use-media-url-refresh.ts:187）直接吞掉（CB 探针 3→4 停 3）。
// 用 hooks-mock：refetch 是可控 spy，能精确让 A 的 refetch **永不落定**（真实 query + adapter 难稳定复现
// 「跨 queryKey 切换后旧 refetch 仍 pending」）。这里数的是 refetchB 这个**独立** spy 被没被调（0 vs 1，
// 单次顺序、无并发 → 不受 cancelRefetch 膨胀影响，二元判定本身就防膨胀）。
describe("HistorySetDialog · FIX5（A pending 切 B，B 的刷新不被 A 的 inFlight 吞）", () => {
  const setOf = (jobId: string, sig: string, refetch: () => Promise<unknown>) => ({
    data: {
      id: jobId,
      category: "ecom_detail", // 无 task_ids → mediaKey=output:index；group 按 (category,set.id) 分
      created_at: "2026-07-10T12:00:00Z",
      status: "completed",
      items: [{ index: 0, download_url: `https://cdn/gone-${jobId}.png?sig=${sig}`, width: 1254, height: 1254 }],
      meta: {}
    },
    isLoading: false,
    isError: false,
    refetch
  });
  const itemForJob = (jobId: string): HistoryItem => ({ ...ITEM, id: jobId, category: "ecom_detail" });

  it("🔴 A 的 refetch 未落定时切到 B、B 首帧报错 → B 仍能发请求（inFlight 按 set 隔离）", () => {
    // A 的 refetch 永不落定 → A 的 group.inFlight 保持 true（模拟 CB 探针「A 保持 pending」）。
    const refetchA = vi.fn().mockReturnValue(new Promise<never>(() => {}));
    hooks.useHistoryImageSet.mockReturnValue(setOf("job-a", "1", refetchA));
    const { rerender } = render(<HistorySetDialog item={itemForJob("job-a")} onClose={() => {}} />);

    fireEvent.error(screen.getByRole("img")); // A 首帧失败 → refetchA 触发并保持 pending
    expect(refetchA).toHaveBeenCalledTimes(1);

    // 切到 B（同一实例）。B 初次查询正常，refetchB 独立可观测。
    const refetchB = vi.fn().mockResolvedValue(undefined);
    hooks.useHistoryImageSet.mockReturnValue(setOf("job-b", "1", refetchB));
    rerender(<HistorySetDialog item={itemForJob("job-b")} onClose={() => {}} />);

    fireEvent.error(screen.getByRole("img")); // B 首帧失败

    // 🔴 承重点：B 有自己的 group（forKey(scopeKey-B)）→ 自己的 inFlight=false → B 发请求。
    // 裸根组时：A 的 refetch 还 pending → 根组 inFlight=true → B 被吞（refetchB 0 次）。
    expect(refetchB).toHaveBeenCalledTimes(1);
  });
});
