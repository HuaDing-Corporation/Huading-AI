import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminTasksPage from "./page";
import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";
import { copy } from "@/lib/copy";

// FIX1 · 重试披露三态承重（BE FIX3 冻结契约）：真打 MSW（不 mock hooks/adapter），逐组合断言结果横幅
// **精确文案**（正则 ^…$，不用模糊断言）。变异哨兵：把横幅的 is_estimate 分支删掉（永远走「将扣费 N 积分」）
// → 按量那条（job-f1）必须变红。⚠️ 模块级 mock 态：每用例重跑**不同**任务（f1/f3/f2 各一次），互不影响。
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
});
afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("任务重跑 · 回执披露三态（精确文案，不许静默扣费）", () => {
  it("按量估算（job-f1）→ 横幅精确：「已重新排队（job-f1），预计扣费约 1,501 积分，最终按实际成片时长结算」（alert 角色）", async () => {
    renderTasks();
    await retryTask("job-f1");
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/^已重新排队（job-f1），预计扣费约 1,501 积分，最终按实际成片时长结算$/);
  });

  it("固定价实扣（job-f3）→ 横幅精确：「已重新排队（job-f3），将扣费 100 积分」（alert 角色）", async () => {
    renderTasks();
    await retryTask("job-f3");
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/^已重新排队（job-f3），将扣费 100 积分$/);
  });

  it("不重复扣费（job-f2）→ 横幅精确：「已重新排队（job-f2），不会重复扣费」（status 角色，非 alert）", async () => {
    renderTasks();
    await retryTask("job-f2");
    const banner = await screen.findByText(/^已重新排队（job-f2），不会重复扣费$/);
    expect(banner).toHaveAttribute("role", "status");
  });
});
