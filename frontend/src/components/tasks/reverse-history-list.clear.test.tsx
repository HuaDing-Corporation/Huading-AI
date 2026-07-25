import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// ── REVERSE-CHARGE-GATE-UI-0001 FIX1 范围2 · 反推历史「清空」承重 ─────────────────────────────
// 不 mock hooks 层：真实 useReversePromptJobs/useClearReversePromptJobs + 真 QueryClient，只在 adapter
// 边界放 spy（数真实请求 + 控成败）。承重门：
//  门1 三种筛选下 scope **各自正确**（toBe 确定值）——变异：写死 "all" → 在图片/视频筛选下必红
//  门2 确认文案**随筛选变**（三条逐字）——变异：改成统一含糊文案 → 必红
//  门3 清空后列表空
//  门4 取消 = **adapter 零调用**（不是"列表没变"）——变异：去掉确认直接清 → 必红

const api = vi.hoisted(() => ({
  listReversePromptJobs: vi.fn(),
  clearReversePromptJobs: vi.fn(),
  deleteReversePromptJob: vi.fn()
}));
vi.mock("@/lib/api/reverse-prompt", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/reverse-prompt")>();
  return { ...actual, ...api };
});
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: { token: "t" }, ready: true, login: vi.fn(), logout: vi.fn() })
}));

import { ReverseHistoryList } from "./reverse-history-list";
import { copy } from "@/lib/copy";
import type { ReversePromptHistoryItem } from "@/lib/api/reverse-prompt";

const job = (id: string, source_kind: "image" | "video"): ReversePromptHistoryItem => ({
  id,
  source_kind,
  status: "succeeded",
  created_at: "2026-07-26T12:00:00Z",
  source_thumbnail_url: null,
  summary: `反推 ${id}`
});

/** 服务端态：清空按 scope 过滤，供门3「清空后列表空」验证。 */
let serverJobs: ReversePromptHistoryItem[] = [];

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const clearBtn = () => screen.getByRole("button", { name: copy.history.reverseClear });
const dialog = () => screen.getByRole("dialog");
const chip = (label: string) => screen.getByRole("button", { name: label });

beforeEach(() => {
  vi.clearAllMocks();
  serverJobs = [job("i1", "image"), job("i2", "image"), job("v1", "video")];
  api.listReversePromptJobs.mockImplementation((input?: { source_kind?: "image" | "video" }) => {
    const items = input?.source_kind ? serverJobs.filter((j) => j.source_kind === input.source_kind) : serverJobs;
    return Promise.resolve({ items: [...items], total: items.length, page: 1, page_size: 20 });
  });
  api.clearReversePromptJobs.mockImplementation((scope: "all" | "image" | "video") => {
    const before = serverJobs.length;
    serverJobs = scope === "all" ? [] : serverJobs.filter((j) => j.source_kind !== scope);
    return Promise.resolve({ deleted_count: before - serverJobs.length });
  });
});
afterEach(() => vi.clearAllMocks());

describe("反推历史 · 清空（真实 hooks + adapter spy）", () => {
  // 🔴 门1：scope 跟随当前筛选，三种各自正确（写死 all 会在 image/video 两条上红）。
  it("门1a：默认「全部」筛选 → scope='all'", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());
    fireEvent.click(clearBtn());
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.history.clearConfirmBtn }));
    await waitFor(() => expect(api.clearReversePromptJobs).toHaveBeenCalledTimes(1));
    expect(api.clearReversePromptJobs).toHaveBeenCalledWith("all");
  });

  it("门1b：切到「图片反推」→ scope='image'（不是 all）", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());
    fireEvent.click(chip(copy.history.reverseKindImage));
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());

    fireEvent.click(clearBtn());
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.history.clearConfirmBtn }));
    await waitFor(() => expect(api.clearReversePromptJobs).toHaveBeenCalledTimes(1));
    expect(api.clearReversePromptJobs).toHaveBeenCalledWith("image"); // 🔴 写死 "all" 在此处红
  });

  it("门1c：切到「视频反推」→ scope='video'（不是 all）", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());
    fireEvent.click(chip(copy.history.reverseKindVideo));
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());

    fireEvent.click(clearBtn());
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.history.clearConfirmBtn }));
    await waitFor(() => expect(api.clearReversePromptJobs).toHaveBeenCalledTimes(1));
    expect(api.clearReversePromptJobs).toHaveBeenCalledWith("video");
  });

  // 🔴 门2：确认文案随筛选变，**把删的是哪一类写死在句子里**（CB 打回 BE 的第一条 P1 + E1 无恢复入口）。
  // 变异：改成统一含糊文案（如都用「将清空历史，无法撤销。」）→ 三条断言里至少两条红。
  it("门2：确认文案随筛选变——全部/图片/视频三句各自逐字，且都点明范围", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());

    // 全部：必须出现「全部」，且说明含图片+视频。
    fireEvent.click(clearBtn());
    expect(within(dialog()).getByText("将清空全部反推历史（图片 + 视频），无法撤销。")).toBeInTheDocument();
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.common.cancel }));

    // 图片：必须点明「图片反推」+「视频不受影响」。
    fireEvent.click(chip(copy.history.reverseKindImage));
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());
    fireEvent.click(clearBtn());
    expect(within(dialog()).getByText("将清空图片反推历史，无法撤销；视频反推历史不受影响。")).toBeInTheDocument();
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.common.cancel }));

    // 视频：必须点明「视频反推」+「图片不受影响」。
    fireEvent.click(chip(copy.history.reverseKindVideo));
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());
    fireEvent.click(clearBtn());
    expect(within(dialog()).getByText("将清空视频反推历史，无法撤销；图片反推历史不受影响。")).toBeInTheDocument();
  });

  // 门3：清空后列表空；且按 scope 清时其余集合不受影响（image 清完 video 仍在）。
  it("门3：清空「图片反推」→ 该集合空；切回全部仍能看到视频反推（其余不受影响）", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(screen.getByText("反推 i1")).toBeInTheDocument());

    fireEvent.click(chip(copy.history.reverseKindImage));
    await waitFor(() => expect(clearBtn()).toBeInTheDocument());
    fireEvent.click(clearBtn());
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.history.clearConfirmBtn }));

    await waitFor(() => expect(screen.queryByText("反推 i1")).not.toBeInTheDocument());
    // 切回全部：视频那条还在（BE 只清了 image）。
    fireEvent.click(chip(copy.history.reverseKindAll));
    await waitFor(() => expect(screen.getByText("反推 v1")).toBeInTheDocument());
  });

  // 🔴 门4a：**确认框存在**——点「清空」必须先弹确认，不能直接清。变异：去掉确认直接清（onClick 里直接
  // mutateAsync、不 setConfirmClear）→ 本条红（无 dialog）。职责单一：只管"有没有确认这道关"，不管请求时序（那是 4b）。
  it("门4a：点清空 → 先弹确认框（不是直接清）", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(screen.getByText("反推 i1")).toBeInTheDocument());

    fireEvent.click(clearBtn());
    expect(screen.queryByRole("dialog")).toBeInTheDocument();
  });

  /**
   * 🔴 门4b：取消 = **一个请求都不发**，且必须**推进到静止点后**再断言。
   *
   * 为什么不能同步断言（CB 的 P1）：React Query 的 `mutationFn` 是**异步调度**的——
   * `mutateAsync()` 同步返回 Promise，但真正调用 mutationFn 要等到微任务队列被冲刷。
   * 同步 `expect(spy).not.toHaveBeenCalled()` 在调度发生**之前**就执行完了，于是「点清空时先把请求发出去、
   * 再弹确认框」这种真危险的实现也能通过（CB 的受控变异 `void clear.mutateAsync(kind); setConfirmClear(true);`）。
   *
   * 为什么**不能用 `waitFor(() => expect(spy).not.toHaveBeenCalled())`**：waitFor 对**否定断言无效**——
   * 第一个 tick 条件就满足、立即返回，与同步断言等价（看起来在等，其实没等）。
   *
   * 选用 `await act(async () => {})`（仓库既有先例：aibrain/composer.test.tsx、PHOTO-FORM-FLAKE-FIX）。
   * **它为什么在结构上覆盖 React Query 的调度**：`act` 的异步形态会 ① await 传入的 thunk，② 冲刷 React 的
   * 工作循环与 effect 队列，③ 在退出前**反复排空微任务队列直到没有新的更新产生**。React Query 的 mutation
   * 调度全部走 Promise 微任务（mutateAsync → executeMutation → mutationFn），因此只要微任务被排空，
   * 「已被排入队列的 mutationFn」必然已经执行——不存在"还没轮到"的窗口。故断言不再抢跑。
   */
  it("门4b：取消后推进到静止点 → clearReversePromptJobs 仍零调用，列表原样", async () => {
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(screen.getByText("反推 i1")).toBeInTheDocument());

    fireEvent.click(clearBtn());
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.common.cancel }));

    // 推进到 React / React Query 的静止点：微任务全部落定、effect 全部冲刷、级联重渲染全部提交。
    await act(async () => {});

    expect(api.clearReversePromptJobs).not.toHaveBeenCalled(); // 危险请求若在路上，此刻必已发出 → 红
    expect(screen.getByText("反推 i1")).toBeInTheDocument(); // 列表也不该被清
  });

  it("清空失败(500) → 弹窗内友好错误、列表不乐观移除", async () => {
    api.clearReversePromptJobs.mockRejectedValue(new Error("boom"));
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(screen.getByText("反推 i1")).toBeInTheDocument());

    fireEvent.click(clearBtn());
    fireEvent.click(within(dialog()).getByRole("button", { name: copy.history.clearConfirmBtn }));

    await waitFor(() => expect(screen.getByText(copy.history.reverseClearFailed)).toBeInTheDocument());
    expect(screen.getByText("反推 i1")).toBeInTheDocument(); // 没被乐观移除
  });

  it("空列表 → 不渲染清空按钮（无可清则不给死按钮）", async () => {
    serverJobs = [];
    wrap(<ReverseHistoryList />);
    await waitFor(() => expect(screen.queryByText("反推 i1")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: copy.history.reverseClear })).not.toBeInTheDocument();
  });
});
