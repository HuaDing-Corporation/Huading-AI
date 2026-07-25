import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// ── HISTORY-CHAT-DELETE-UI-0001 · 图片历史删除/清空的**真实网络承重** ─────────────────────────
// 与 history-grid.test.tsx（hooks mock，测渲染/派生）分工：本文件**不 mock hooks**，用真实
// useHistoryImages/useDeleteHistoryImageSet/useClearHistoryImages + 真实 QueryClient，只在 adapter
// 边界放 spy —— 数的是**真实打了几次后端、发的什么**（门 4「取消 = 一个请求都不发」只有这样才测得准：
// 断言 fetch 未被调用，而不是"断言列表没变"）。
// 承重门：①删一条 → 该条消失、其余一条不少（toBe 确定计数）；②清空当前分类 → 只清该分类（请求带 category）；
// ④取消 = 零请求；⑤失败 → 友好错误 + **列表不乐观移除**。

const adapter = vi.hoisted(() => ({
  listHistoryImages: vi.fn(),
  getHistoryImageSet: vi.fn(),
  deleteHistoryImageSet: vi.fn(),
  clearHistoryImages: vi.fn()
}));
vi.mock("@/lib/api/history-images", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/history-images")>();
  return { ...actual, ...adapter };
});
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { HistoryGrid } from "./history-grid";
import { copy } from "@/lib/copy";
import type { HistoryItem } from "@/lib/api/history-images";

const card = (id: string, category = "image_gen"): HistoryItem => ({
  id,
  category,
  title: `图 ${id}`,
  cover_url: `https://cdn/${id}.png`,
  created_at: "2026-07-24T12:00:00Z",
  status: "completed",
  item_count: 1
});

/** 服务端态：一份可变列表，adapter spy 从它派生 → 删除后 refetch 能拿到"真的少了一条"。 */
let serverItems: HistoryItem[] = [];

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const cards = () => screen.getAllByTestId("history-card");
const deleteBtns = () => screen.getAllByRole("button", { name: copy.history.deleteItem });

beforeEach(() => {
  serverItems = [card("h1"), card("h2"), card("h3")];
  adapter.listHistoryImages.mockImplementation(() =>
    Promise.resolve({ items: [...serverItems], total: serverItems.length, page: 1, page_size: 20 })
  );
  adapter.getHistoryImageSet.mockResolvedValue(undefined);
  adapter.deleteHistoryImageSet.mockImplementation((_c: string, id: string) => {
    serverItems = serverItems.filter((i) => i.id !== id);
    return Promise.resolve({ deleted: true });
  });
  adapter.clearHistoryImages.mockImplementation((c: string) => {
    const before = serverItems.length;
    serverItems = serverItems.filter((i) => i.category !== c);
    return Promise.resolve({ deleted_count: before - serverItems.length });
  });
});
afterEach(() => vi.clearAllMocks());

describe("HistoryGrid 删除/清空（真实 hooks + adapter spy）", () => {
  // 🔴 门 1：删一条 → 该条从列表消失、**其余条目一条不少**（确定计数，不是"至少少了一条"）。
  it("门1：删一条 → 该条消失且其余一条不少（3 → 2，且删的是点的那条）", async () => {
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    fireEvent.click(deleteBtns()[1]); // 删第 2 条 h2
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(cards()).toHaveLength(2));
    expect(adapter.deleteHistoryImageSet).toHaveBeenCalledTimes(1);
    expect(adapter.deleteHistoryImageSet).toHaveBeenCalledWith("image_gen", "h2");
    expect(screen.getByText("图 h1")).toBeInTheDocument();
    expect(screen.getByText("图 h3")).toBeInTheDocument();
    expect(screen.queryByText("图 h2")).not.toBeInTheDocument();
  });

  // 🔴 门 2：清空**当前分类** —— 请求必须带上当前 category（mock/BE 侧据此只清该类；变异"清全部"在 mock 层测）。
  it("门2：清空当前分类 → 只发一次带 category 的清空请求，列表清空", async () => {
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.historyImages.clearCategory) }));
    fireEvent.click(screen.getByRole("button", { name: copy.history.clearConfirmBtn }));

    await waitFor(() => expect(screen.getByText(copy.historyImages.empty)).toBeInTheDocument());
    expect(adapter.clearHistoryImages).toHaveBeenCalledTimes(1);
    expect(adapter.clearHistoryImages).toHaveBeenCalledWith("image_gen"); // ← 带分类，不是无参"清全部"
  });

  // 🔴 门 4：二次确认取消 = **一个请求都不发**（断言 adapter 未被调用，而非"列表没变"——后者删了也可能看着没变）。
  it("门4：删除确认里点取消 → deleteHistoryImageSet 一次都没被调用，列表原样", async () => {
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    fireEvent.click(deleteBtns()[0]);
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));

    expect(adapter.deleteHistoryImageSet).not.toHaveBeenCalled();
    expect(cards()).toHaveLength(3);
  });

  it("门4b：清空确认里点取消 → clearHistoryImages 一次都没被调用", async () => {
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.historyImages.clearCategory) }));
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));

    expect(adapter.clearHistoryImages).not.toHaveBeenCalled();
    expect(cards()).toHaveLength(3);
  });

  // 🔴 门 5：删除失败 → 友好错误 + **列表不乐观移除**（绝不留"看起来删了其实没删"的界面）。
  it("门5：删除失败(500) → 弹窗内友好错误、列表仍 3 条（不乐观移除）", async () => {
    adapter.deleteHistoryImageSet.mockRejectedValue(new Error("boom"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    fireEvent.click(deleteBtns()[0]);
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(screen.getByText(copy.history.deleteFailed)).toBeInTheDocument());
    expect(cards()).toHaveLength(3); // 一条没少
  });

  it("门5b：清空失败(500) → 友好错误、列表仍 3 条", async () => {
    adapter.clearHistoryImages.mockRejectedValue(new Error("boom"));
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.historyImages.clearCategory) }));
    fireEvent.click(screen.getByRole("button", { name: copy.history.clearConfirmBtn }));

    await waitFor(() => expect(screen.getByText(copy.history.clearFailed)).toBeInTheDocument());
    expect(cards()).toHaveLength(3);
  });

  // 删除入口的**位置**承重（#218 教训：徽标与删除抢同一个角 → 有徽标的删不掉）：
  // 删除按钮必须在**卡片底部操作行**、与缩略图上的张数角标物理隔离。变异：把删除按钮塞回缩略图内 → 本条红。
  it("位置：删除按钮在底部操作行、不在缩略图按钮内（#218 同角竞争的结构性防线）", async () => {
    wrap(<HistoryGrid category="image_gen" />);
    await waitFor(() => expect(cards()).toHaveLength(3));

    const del = deleteBtns()[0];
    const thumbBtn = screen.getAllByRole("button", { name: copy.historyImages.openLarge })[0];
    expect(thumbBtn.contains(del)).toBe(false); // 不在缩略图（角标所在容器）里
    // 与「查看详情」同处一行 → 底部操作行。
    const row = del.parentElement as HTMLElement;
    expect(row.textContent).toContain(copy.historyImages.viewDetail);
  });
});
