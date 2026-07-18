import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TenantDetailDialog } from "./tenant-detail";
import { fetchAdminAudit, fetchAdminTenantDetail } from "@/lib/api/admin-console";
import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";
import { copy } from "@/lib/copy";
import { resetAdminConsole } from "@/mocks/handlers";

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

// 🔴 ADMIN-MOCK-STORE-RESET-0001：与 admin-console.test.ts 同口径 —— admin mock store 每条测试前重置，顺序无关。
// （本文件的「取消不调」原本断言 total=25000 / 审计 1 条，赌的正是「上一条 +5000 的用例已经跑过」。）
beforeEach(() => {
  localStorage.clear();
  authStore.clear();
  resetAdminConsole();
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
    expect(detail.tenant.subscription?.total).toBe(20000); // acme 的 seed 值：取消 → 分文未动
    const audit = await fetchAdminAudit({ action: "credits_adjust", page: 1, page_size: 20 });
    // 0 才是这条测试名字里那个「无新审计」。重置前它断言 1 =「仍是上一用例那一条」——数的是残留而不是新增：
    // 顺序执行时它确实能红（1 残留 + 1 误写 = 2），但它一旦被排到 +5000 那条**前面**（shuffle 下就会），
    // 残留是 0，取消若真误写一条审计 → 总数正好 1 → **绿**。断言的意义随座位变，这才是要治的。
    expect(audit.total).toBe(0);
  });

  // 🔴 P1-2 前端承重：理由 501 字 → 提交前拦住（friendly 提示 + 确认弹窗不弹 = 不发请求）。
  // 注：jsdom 的 fireEvent.change 不受 maxLength 限制 → 正好测「提交前长度校验」这层兜底（粘贴场景）。
  it("理由 501 字 → 前端拦截：提示「理由不能超过 500 字」、确认弹窗不弹、不调接口", async () => {
    renderDetail("ten-acme");
    await screen.findByText(copy.admin.detailRecentTasks); // 详情加载完

    fireEvent.change(screen.getByLabelText(copy.admin.creditsDelta), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText(copy.admin.creditsReason), { target: { value: "长".repeat(501) } });
    fireEvent.click(screen.getByRole("button", { name: copy.admin.creditsAdjust }));
    expect(await screen.findByText(copy.admin.creditsReasonTooLong)).toBeInTheDocument();
    expect(screen.queryByText(copy.admin.creditsConfirmTitle)).not.toBeInTheDocument(); // 弹窗未开 = 请求未发
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
