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
