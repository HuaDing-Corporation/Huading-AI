import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// ── MEDIA-URL-REFRESH-CONVERGE-0001 · FIX1 · P1-2 的**真实网络承重** ───────────────────────────
//
// 🔴 任务包硬门 2：「承重测试改用**真实 queryFn / network 调用次数**，**不是 `refetch` 的 spy 调用次数**
//    —— 那正是让你误判的东西。」
//
// 上一版所有 URL 过期测试都 mock 掉 `@/lib/api/hooks` 并数 `refetch` spy 的调用次数。那个数字
// **系统性地低估了真实请求数**：
//   · 它数不到 TanStack 内部的行为 —— 默认 `cancelRefetch:true` 时，两次并发 refetch 会让 queryFn
//     被调 **3** 次（第二次**中止并重启**第一次）。我自己跑探针复现：默认=3 / cancelRefetch:false=2。
//   · 于是「refetch spy 被调 2 次」看起来温和，真实网络却是 3 次；而 N 张卡各持一份预算时是 2×N 起。
//
// 本文件**不 mock hooks 层** —— 用真实的 `useHistoryImages` + 真实 QueryClient，只把最底层的
// adapter（`listHistoryImages`，即真实 HTTP 的边界）换成 spy。数的是**它**被调了几次 = 真实打了几次后端。
//
// ⚠️ 与 history-grid.test.tsx 的分工：那边用 hooks mock 测**渲染/派生/导电**（快、稳），
//    这边专测**请求数**。两者都需要 —— 前者证明防线导电，后者证明防线不打爆后端。

const adapter = vi.hoisted(() => ({ listHistoryImages: vi.fn(), getHistoryImageSet: vi.fn() }));
vi.mock("@/lib/api/history-images", async (importOriginal) => {
  // 只替换两个 fetch 函数，保留 HISTORY_CATEGORIES / 类型 / historyImageDimensions 等真实导出。
  const actual = await importOriginal<typeof import("@/lib/api/history-images")>();
  return { ...actual, ...adapter };
});
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { HistoryGrid } from "./history-grid";
import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

const card = (i: number, sig: string): HistoryItem => ({
  id: `h${i}`,
  category: "image_gen",
  title: `图 ${i}`,
  cover_url: `https://cdn/cover-${i}.png?sig=${sig}`,
  created_at: "2026-07-10T12:00:00Z",
  status: "completed",
  item_count: 1
});

/** 一页 N 条，全部带同一个 sig（模拟「整批 presign 同时签发、同时过期」）。 */
const page = (n: number, sig: string) => ({
  items: Array.from({ length: n }, (_, i) => card(i + 1, sig)),
  total: n,
  page: 1,
  page_size: 20
});

/** 一坏（h1 永远 gone）+ N-1 好（h2..hN 每轮换新、能加载）。用于 P1-2 的「部分成功干扰」场景。 */
const mixedPage = (n: number, sig: string) => ({
  items: [
    { ...card(1, sig), cover_url: `https://cdn/gone.png?sig=${sig}` },
    ...Array.from({ length: n - 1 }, (_, i) => card(i + 2, sig))
  ],
  total: n,
  page: 1,
  page_size: 20
});

function wrap(ui: ReactNode) {
  // gcTime:Infinity + staleTime:0 无关紧要 —— 这里数的是 queryFn 实际被调的次数。
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.clearAllMocks());

describe("HistoryGrid · 真实 network 次数（P1-2：预算的作用域 = query 的作用域）", () => {
  it("🔴 8 张卡的 presign 同时过期 → 真实只打后端 1 次（上一版是 8 次起，且并发时更多）", async () => {
    adapter.listHistoryImages.mockResolvedValue(page(8, "1"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(8));

    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(1); // 初次加载

    // 整批 URL 同时失效 —— 8 个 error 在同一 tick 涌进来，URL **各不相同**（去重挡不住）
    act(() => {
      screen.getAllByRole("img").forEach((img) => fireEvent.error(img));
    });
    await act(async () => {});

    // 一次 refetch 就把 8 张的 cover_url 全刷回来了；其余 7 次是纯重复请求。
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(2); // 1 初次 + 1 重取
  });

  it("🔴 对象已删（每轮签出新 URL、个个失效）→ 全网格合计最多 2 次重取，不是 2×8", async () => {
    adapter.listHistoryImages.mockResolvedValue(page(8, "1"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(8));
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(1);

    // 三轮「整批碎 → 重取 → BE 签出新的一批 → 仍然个个 404」
    for (let round = 2; round <= 4; round++) {
      adapter.listHistoryImages.mockResolvedValue(page(8, String(round)));
      act(() => {
        screen.getAllByRole("img").forEach((img) => fireEvent.error(img));
      });
      await act(async () => {});
    }

    // 初次 1 + 封顶 2 = 3。上一版：每张卡各 2 次 = 16 次重取（并发时 TanStack 还会再放大）。
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(3);
  });

  // 🔴 FIX2 的 P1-2 真实 network 承重：**部分成功干扰**。
  // 1 张坏（对象删了、每轮签新 URL 仍 404）+ 7 张好（每轮换新、正常加载）。健康兄弟的 load 若清掉
  // 坏图的失败计数（= 上一版把清零提到 query 级），坏图就**永远打不到封顶** → 每轮都 refetch，无限。
  // 现有 network 测试只覆盖「全图一起失败」，覆盖不到这个 —— 这条专补它（Codex B 的场景）。
  it("🔴 1 坏 7 好、健康兄弟每轮 load × 5 → 坏图封顶仍 2（健康兄弟不替坏图清账）", async () => {
    adapter.listHistoryImages.mockResolvedValue(mixedPage(8, "1"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(8));
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(1);

    const goodAlts = ["图 2", "图 3", "图 4", "图 5", "图 6", "图 7", "图 8"];
    for (let round = 2; round <= 6; round++) {
      adapter.listHistoryImages.mockResolvedValue(mixedPage(8, String(round)));
      act(() => fireEvent.error(screen.getByAltText("图 1"))); // 坏图失效
      await act(async () => {}); // 重取往返：新一批 URL 进来（坏图仍坏，好图换新）
      act(() => goodAlts.forEach((alt) => fireEvent.load(screen.getByAltText(alt)))); // 健康兄弟全部加载成功
    }

    // 坏图独立爬到封顶 2 就停：初次 1 + 坏图 2 = 3。
    // 上一版（清零 query 级）：健康兄弟每轮清掉坏图计数 → 坏图永不封顶 → 1 + 5 = 6。
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(3);
  });

  it("大图弹窗与卡片共用同一份预算（弹窗碎了 = 那张卡也碎了，一次重取两边一起救）", async () => {
    adapter.listHistoryImages.mockResolvedValue(page(3, "1"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(3));
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getAllByRole("button", { name: copy.historyImages.openLarge })[0]);

    // 弹窗里的大图 + 它背后那张卡，同一个 URL、同一份预算
    act(() => {
      screen.getAllByRole("img").forEach((img) => fireEvent.error(img));
    });
    await act(async () => {});

    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(2);
  });

  it("加载成功即清零 → 长会话里再过期仍能再救（封顶不误伤正常的二次过期）", async () => {
    /** 整批碎一次，并等**新一批 URL 真的进到 DOM 里**（否则下一轮 error 报的还是旧 URL，会被去重挡掉）。 */
    const breakAllAndAwaitFresh = async (sig: string) => {
      adapter.listHistoryImages.mockResolvedValue(page(2, sig));
      act(() => screen.getAllByRole("img").forEach((img) => fireEvent.error(img)));
      await waitFor(() =>
        expect(screen.getAllByRole("img")[0].getAttribute("src")).toContain(`sig=${sig}`)
      );
    };

    adapter.listHistoryImages.mockResolvedValue(page(2, "1"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));

    // 两轮失效 → 已到封顶
    await breakAllAndAwaitFresh("2");
    await breakAllAndAwaitFresh("3");
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(3); // 1 初次 + 2 重取（封顶）

    // 这批 URL 真的加载出来了 → 预算清零
    act(() => screen.getAllByRole("img").forEach((img) => fireEvent.load(img)));

    // 很久以后它们自己过期了 → 还能再救（若不清零，这里会哑 → 用户对着碎图干瞪眼）
    await breakAllAndAwaitFresh("9");
    expect(adapter.listHistoryImages).toHaveBeenCalledTimes(4);
  });
});
