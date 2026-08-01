import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminTasksPage from "./page";
import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";
import { copy } from "@/lib/copy";
import { resetAdminConsole } from "@/mocks/handlers";

// FIX1 · 重试披露三态承重（BE FIX3 冻结契约）：真打 MSW（不 mock hooks/adapter），逐组合断言结果横幅
// **精确文案**（正则 ^…$，不用模糊断言）。变异哨兵：把横幅的 is_estimate 分支删掉（永远走「将扣费 N 积分」）
// → 按量那条（job-f1）必须变红。
//
// 🔴 ADMIN-MOCK-STORE-RESET-0001：这里原本写「⚠️ 模块级 mock 态：每用例重跑**不同**任务（f1/f3/f2 各一次），
// 互不影响」。「互不影响」当时是真的，但那是**数据凑巧**（三条用例正好各挑了一个任务），不是机制——
// 重跑会就地改写 status/retryable，谁再加一条碰同一个任务的用例，它就随座位表变。改成 beforeEach 重置：
// 「互不影响」从「碰巧成立」变成结构上成立。（实测：本文件在 shuffle 下不红，加重置也不改变任何断言。）
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin/tasks"
}));

function renderTasks() {
  authStore.set({ token: "t", tenantId: "ten-mock", userId: "u-mock", role: "admin" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <AdminTasksPage />
      </AuthProvider>
    </QueryClientProvider>
  );
}

async function retryTask(taskId: string) {
  const idCell = await screen.findByText(taskId);
  const row = idCell.closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: copy.admin.retryTask }));
  // 确认弹窗：通用口径说明（披露字段在回执里，确认前不承诺具体组合）。
  await screen.findByText(copy.admin.retryTitle);
  expect(screen.getByText(copy.admin.retryConfirmNote)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: copy.admin.retryBtn }));
}

beforeEach(() => {
  localStorage.clear();
  authStore.clear();
  resetAdminConsole();
});
afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

// 🔴 FIX1（Codex B #191 P1）：定向 scoped timeout 15s，**不放宽全局**（全局保持默认 5s）。
// 这三条是 React 渲染测试（render + AdminTasksPage + MSW 往返），在 shuffle 高负载窗口偶发 5s 超时——
// 那是**负载噪声、非顺序依赖**（单跑无限稳定绿，见 ADMIN-MOCK-STORE-RESET-0001 §二）。全局放宽会掩盖别的
// 真实慢测；scoped 只兜住这一处。vitest 2.1.9：describe 第二参 options.timeout 合并进本 suite 全部子测试（已实测）。
describe("任务重跑 · 回执披露三态（精确文案，不许静默扣费）", { timeout: 15000 }, () => {
  it("按量估算（job-f1）→ 横幅精确：「已重新排队（job-f1），预计扣费约 1,501 积分，最终按实际成片时长结算」（alert 角色）", async () => {
    renderTasks();
    await retryTask("job-f1");
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/^已重新排队（job-f1），预计扣费约 1,501 积分，最终按实际成片时长结算$/);
  });

  it("固定价实扣（job-f3）→ 横幅精确：「已重新排队（job-f3），将扣费 150 积分」（alert 角色）", async () => {
    renderTasks();
    await retryTask("job-f3");
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/^已重新排队（job-f3），将扣费 150 积分$/);
  });

  it("不重复扣费（job-f2）→ 横幅精确：「已重新排队（job-f2），不会重复扣费」（status 角色，非 alert）", async () => {
    renderTasks();
    await retryTask("job-f2");
    const banner = await screen.findByText(/^已重新排队（job-f2），不会重复扣费$/);
    expect(banner).toHaveAttribute("role", "status");
  });
});
