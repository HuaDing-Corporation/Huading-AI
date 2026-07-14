import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TenantDetailDialog } from "./tenant-detail";
import { fetchAdminAudit, fetchAdminTenantDetail } from "@/lib/api/admin-console";
import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";
import { copy } from "@/lib/copy";

// 余额调整承重（ADMIN-CONSOLE-UI-0001 §四.3）：真打 MSW（不 mock hooks/adapter），资金红线全钉：
// ① 确认弹窗显示「当前余额 → 调整后余额」确定值；② **确认恰调一次**（同一 tick 连点两次 → 余额只加一次、
// 审计恰 1 条——ref 闸承重）；③ 取消不调（余额分文未动、审计 0 条）；④ 422（扣到低于已用+预留）中文原样展示。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin/tenants"
}));

function renderDetail(tenantId: string) {
  authStore.set({ token: "t", tenantId: "ten-mock", userId: "u-mock", role: "admin" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <TenantDetailDialog tenantId={tenantId} onClose={() => {}} />
      </AuthProvider>
    </QueryClientProvider>
  );
}

async function openCreditsConfirm(delta: string, reason: string) {
  await screen.findByText(copy.admin.detailRecentTasks); // 详情加载完
  fireEvent.change(screen.getByLabelText(copy.admin.creditsDelta), { target: { value: delta } });
  fireEvent.change(screen.getByLabelText(copy.admin.creditsReason), { target: { value: reason } });
  fireEvent.click(screen.getByRole("button", { name: copy.admin.creditsAdjust }));
  await screen.findByText(copy.admin.creditsConfirmTitle);
}

beforeEach(() => {
  localStorage.clear();
  authStore.clear();
});
afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("TenantDetailDialog · 余额调整（资金安全）", () => {
  it("确认弹窗显示「当前余额 20,000 → 调整后 25,000」；连点确认两次 → 恰调一次（余额 25000 非 30000、审计恰 1 条）", async () => {
    renderDetail("ten-acme");
    await openCreditsConfirm("5000", "线下打款充值");
    // 资金红线：前 → 后 确定值。
    expect(screen.getByText(copy.admin.creditsBeforeAfter(20000, 25000))).toBeInTheDocument();
    const confirmBtn = screen.getByRole("button", { name: copy.admin.creditsConfirmBtn });
    // 同一 tick 连点两次（isPending 闭包仍旧值——ref 闸必须吞掉第二次）。
    fireEvent.click(confirmBtn);
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(screen.getByText(copy.admin.creditsDone)).toBeInTheDocument());
    // 请求级确证：只调了一次（+5000 恰一次 → 25000；调两次会是 30000）。
    const detail = await fetchAdminTenantDetail("ten-acme");
    expect(detail.tenant.subscription?.total).toBe(25000);
    const audit = await fetchAdminAudit({ action: "credits_adjust", page: 1, page_size: 20 });
    expect(audit.total).toBe(1);
    expect(audit.items[0]).toMatchObject({ before: { id: "sub-acme", total: 20000, remaining: 14000 }, after: { id: "sub-acme", total: 25000, remaining: 19000 } });
  });

  it("取消不调：打开确认后点取消 → 余额分文未动、无新审计", async () => {
    renderDetail("ten-acme");
    await openCreditsConfirm("-1000", "误操作回收");
    fireEvent.click(screen.getByRole("button", { name: copy.common.cancel }));
    await waitFor(() => expect(screen.queryByText(copy.admin.creditsConfirmTitle)).not.toBeInTheDocument());
    const detail = await fetchAdminTenantDetail("ten-acme");
    expect(detail.tenant.subscription?.total).toBe(25000); // 上一用例后的值，取消未再动
    const audit = await fetchAdminAudit({ action: "credits_adjust", page: 1, page_size: 20 });
    expect(audit.total).toBe(1); // 仍是上一用例那一条
  });

  it("下限保护 422：gamma 扣 -300 → 弹窗内原样展示 BE 中文 message，余额不变", async () => {
    renderDetail("ten-gamma");
    await openCreditsConfirm("-300", "回收");
    fireEvent.click(screen.getByRole("button", { name: copy.admin.creditsConfirmBtn }));
    expect(await screen.findByText("扣减后额度会低于已用+预留，无法执行。")).toBeInTheDocument();
    const detail = await fetchAdminTenantDetail("ten-gamma");
    expect(detail.tenant.subscription?.total).toBe(5000);
  });

  it("平台租户自己（ten-mock）：停用按钮置灰 + 「平台租户不可停用」提示（前端先拦一道）", async () => {
    renderDetail("ten-mock");
    await screen.findByText(copy.admin.detailRecentTasks);
    expect(screen.getByRole("button", { name: copy.admin.statusDisable })).toBeDisabled();
    expect(screen.getByText(copy.admin.statusPlatformProtected)).toBeInTheDocument();
  });
});
