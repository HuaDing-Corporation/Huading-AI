import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// ── HISTORY-CHAT-DELETE-UI-0001 · 智脑会话删除/清空承重 ────────────────────────────────────
// 不 mock hooks 层：用真实 useConversations/useDeleteConversation/useClearConversations + 真 QueryClient，
// 只在 aibrain/api 边界放 spy（数真实请求 + 控成败）。
// 承重门：①删一条其余不少；③**删当前打开的会话 → 不白屏**（自动切走，且无 console error）；
// ④取消 = 零请求；⑤失败 → 友好错误 + 列表不乐观移除。

const api = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  clearConversations: vi.fn()
}));
vi.mock("@/lib/aibrain/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/aibrain/api")>();
  return { ...actual, ...api };
});
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { ConversationList } from "./conversation-list";
import { copy } from "@/lib/copy";

type Conv = { id: string; title: string; updated_at: string };
let serverConvs: Conv[] = [];

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const delBtn = (title: string) => screen.getByRole("button", { name: `${copy.aibrain.deleteChat}：${title}` });

beforeEach(() => {
  serverConvs = [
    { id: "c1", title: "会话一", updated_at: "2026-07-24T12:00:00Z" },
    { id: "c2", title: "会话二", updated_at: "2026-07-24T11:00:00Z" },
    { id: "c3", title: "会话三", updated_at: "2026-07-24T10:00:00Z" }
  ];
  api.listConversations.mockImplementation(() => Promise.resolve([...serverConvs]));
  api.deleteConversation.mockImplementation((id: string) => {
    serverConvs = serverConvs.filter((c) => c.id !== id);
    return Promise.resolve({ deleted: true });
  });
  api.clearConversations.mockImplementation(() => {
    const n = serverConvs.length;
    serverConvs = [];
    return Promise.resolve({ deleted_count: n });
  });
});
afterEach(() => vi.clearAllMocks());

describe("ConversationList 删除/清空（真实 hooks + api spy）", () => {
  // 门 1：删一条 → 该条消失、其余一条不少。
  it("门1：删一条 → 3 → 2，其余会话仍在", async () => {
    wrap(<ConversationList activeId="c1" onSelect={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("会话二")).toBeInTheDocument());

    fireEvent.click(delBtn("会话二"));
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(screen.queryByText("会话二")).not.toBeInTheDocument());
    expect(api.deleteConversation).toHaveBeenCalledWith("c2");
    expect(screen.getByText("会话一")).toBeInTheDocument();
    expect(screen.getByText("会话三")).toBeInTheDocument();
  });

  // 🔴 门 3（核心）：删的是**当前打开的会话** → 必须自动切走，不留一个指向已删会话的 id（否则右侧白屏）。
  // 变异：去掉 onConfirmDelete 里的「若删的是 activeId 则 onSelect(切走)」→ 本条红。
  it("门3：删当前打开的会话 → 自动切到剩余会话（onSelect 收到新 id，不是已删的那个）", async () => {
    const onSelect = vi.fn();
    wrap(<ConversationList activeId="c1" onSelect={onSelect} />);
    await waitFor(() => expect(screen.getByText("会话一")).toBeInTheDocument());

    fireEvent.click(delBtn("会话一")); // 删的正是当前打开的
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(onSelect).toHaveBeenCalled());
    const arg = onSelect.mock.calls.at(-1)?.[0];
    expect(arg).not.toBe("c1"); // 绝不停留在已删会话上
    expect(arg).toBe("c2"); // 切到剩余列表的第一条（updated_at 倒序 = 最近）
  });

  it("门3b：删掉最后一个会话（且是当前） → onSelect(null) 走空态，不白屏", async () => {
    serverConvs = [{ id: "only", title: "唯一会话", updated_at: "2026-07-24T12:00:00Z" }];
    const onSelect = vi.fn();
    wrap(<ConversationList activeId="only" onSelect={onSelect} />);
    await waitFor(() => expect(screen.getByText("唯一会话")).toBeInTheDocument());

    fireEvent.click(delBtn("唯一会话"));
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(null));
    await waitFor(() => expect(screen.getByText(copy.aibrain.conversationsEmpty)).toBeInTheDocument());
  });

  it("门3c：删的**不是**当前会话 → 不动 activeId（不打扰用户正在看的对话）", async () => {
    const onSelect = vi.fn();
    wrap(<ConversationList activeId="c1" onSelect={onSelect} />);
    await waitFor(() => expect(screen.getByText("会话三")).toBeInTheDocument());

    fireEvent.click(delBtn("会话三"));
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(screen.queryByText("会话三")).not.toBeInTheDocument());
    expect(onSelect).not.toHaveBeenCalled();
  });

  // 门 4：取消 = 一个请求都不发。
  it("门4：删除确认里点取消 → deleteConversation 一次都没被调用", async () => {
    wrap(<ConversationList activeId="c1" onSelect={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("会话一")).toBeInTheDocument());

    fireEvent.click(delBtn("会话一"));
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));

    // 🔴 REVERSE-CLEAR-HISTORY-UI-0001-FIX1 同源修复：**先推进到静止点再断言零调用**。
    // 同步断言跑在 React Query 调度 mutationFn 之前 → 「先发请求再弹确认框」也能通过（CB 在 #229 证明）；
    // waitFor 对否定断言无效（第一 tick 即满足）。act 的异步形态排空微任务直到无新更新，覆盖 RQ 的调度。
    await act(async () => {});

    expect(api.deleteConversation).not.toHaveBeenCalled();
    expect(screen.getByText("会话一")).toBeInTheDocument();
  });

  // 门 5：失败 → 友好错误 + 列表不乐观移除。
  it("门5：删除失败(500) → 弹窗内友好错误、会话仍在列表里", async () => {
    api.deleteConversation.mockRejectedValue(new Error("boom"));
    wrap(<ConversationList activeId="c1" onSelect={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("会话二")).toBeInTheDocument());

    fireEvent.click(delBtn("会话二"));
    fireEvent.click(screen.getByRole("button", { name: copy.history.deleteConfirmBtn }));

    await waitFor(() => expect(screen.getByText(copy.aibrain.deleteChatFailed)).toBeInTheDocument());
    expect(screen.getByText("会话二")).toBeInTheDocument(); // 没被乐观移除
  });

  // 清空全部：走空态 + 无条件切空（清空必然包含当前会话）。
  it("清空全部 → 列表空 + onSelect(null)；取消则零请求", async () => {
    const onSelect = vi.fn();
    wrap(<ConversationList activeId="c1" onSelect={onSelect} />);
    await waitFor(() => expect(screen.getByText("会话一")).toBeInTheDocument());

    // 先测取消（门 4 的清空侧）。
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.aibrain.clearChats) }));
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));
    await act(async () => {}); // 同上：静止点后再断言零调用
    expect(api.clearConversations).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.aibrain.clearChats) }));
    fireEvent.click(screen.getByRole("button", { name: copy.history.clearConfirmBtn }));
    await waitFor(() => expect(screen.getByText(copy.aibrain.conversationsEmpty)).toBeInTheDocument());
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});
