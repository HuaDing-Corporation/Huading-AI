import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentType } from "react";

import AuditPage from "./audit/page";
import TasksPage from "./tasks/page";
import TenantsPage from "./tenants/page";
import UsagePage from "./usage/page";
import VoiceSlotsPage from "./voice-slots/page";
import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";

// SELECT-ARIA-LABEL-FIX-0001 页面级护栏网：五个 admin 页面各断言一处筛选器**有确定的可及名**。
// 谁再把共享组件 SelectTrigger 的 aria-label 透传改坏，这五条**一起红**（比单组件测试更难被绕过）。
// 断言用确定文案 + role=combobox（Radix 触发器角色），不用 toBeTruthy 之类。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin"
}));

function renderPage(Page: ComponentType) {
  authStore.set({ token: "t", tenantId: "ten-mock", userId: "u-mock", role: "admin" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Page />
      </AuthProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  localStorage.clear();
  authStore.clear();
});
afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("admin 筛选器可及名（SelectTrigger aria-label 透传网）", () => {
  it("审计日志：动作筛选器可及名「动作」", async () => {
    renderPage(AuditPage);
    expect(await screen.findByRole("combobox", { name: "动作" })).toBeInTheDocument();
  });

  it("任务监控：状态筛选器可及名「状态」", async () => {
    renderPage(TasksPage);
    expect(await screen.findByRole("combobox", { name: "状态" })).toBeInTheDocument();
  });

  it("租户管理：套餐筛选器可及名「套餐」", async () => {
    renderPage(TenantsPage);
    expect(await screen.findByRole("combobox", { name: "套餐" })).toBeInTheDocument();
  });

  it("用量明细：capability 筛选器可及名「capability」", async () => {
    renderPage(UsagePage);
    expect(await screen.findByRole("combobox", { name: "capability" })).toBeInTheDocument();
  });

  it("音色槽位已退役写操作，因此只显示库存且没有分配筛选器", async () => {
    renderPage(VoiceSlotsPage);
    expect(await screen.findByText(/旧槽位分配入口已退役/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "租户" })).not.toBeInTheDocument();
  });
});
