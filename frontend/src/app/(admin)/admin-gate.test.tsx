import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth/auth-context";
import { authStore } from "@/lib/auth/store";
import { copy } from "@/lib/copy";
import AdminLayout from "./admin/layout";

// 管理后台门禁整链承重（ADMIN-CONSOLE-UI-0001 §四.1）：**真打 MSW /me**（AuthProvider mount 刷新拉真实
// permissions），不手工造 session.permissions——mock 给非平台态硬塞 admin_console 的变异会让本文件变红。
// 正断言先立（友好页文案可见），再做「不出现任何租户数据」的负断言（防空过）。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin/tenants"
}));

function seedSession() {
  // 已登录会话（缓存无 permissions）→ AuthProvider mount fetchMe 拉真实 /me 派生权限。
  authStore.set({ token: "t", tenantId: "ten-mock", userId: "u-mock", role: "admin" });
}

beforeEach(() => {
  localStorage.clear();
  authStore.clear();
});
afterEach(() => {
  localStorage.clear();
});

describe("/admin 门禁（真实 /me 驱动）", () => {
  it("非平台账号（新注册态：role=admin + free）→ 友好页「仅平台管理员可访问」可见，后台内容与租户数据不渲染", async () => {
    localStorage.setItem("hd_mock_platform", "0");
    localStorage.setItem("hd_mock_plan", "free");
    seedSession();
    render(
      <AuthProvider>
        <AdminLayout>
          <div data-testid="admin-content">贝塔传媒</div>
        </AdminLayout>
      </AuthProvider>
    );
    // 先等 /me 落定（mount 刷新写回 user）——负断言必须在权限收敛**之后**做，否则变异
    // （mock 给非平台硬塞 admin_console）时 /me 晚到、断言早跑完 → 假绿。
    await waitFor(() => expect(authStore.get()?.user).toBeTruthy());
    // 正断言先立：友好页文案可见（权限已收敛，仍是无权限态）。
    expect(await screen.findByText(copy.admin.gateTitle)).toBeInTheDocument();
    expect(screen.queryByTestId("admin-content")).not.toBeInTheDocument();
    // 负断言：页面上不出现任何租户数据/后台导航。
    expect(screen.queryByText("贝塔传媒")).not.toBeInTheDocument();
    expect(screen.queryByText(copy.admin.navTenants)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: copy.admin.gateBack })).toHaveAttribute("href", "/");
  });

  it("平台账号（默认）→ 后台壳 + 内容渲染，无友好页", async () => {
    seedSession();
    render(
      <AuthProvider>
        <AdminLayout>
          <div data-testid="admin-content">内容</div>
        </AdminLayout>
      </AuthProvider>
    );
    expect(await screen.findByTestId("admin-content")).toBeInTheDocument();
    expect(screen.getByText(copy.admin.navTenants)).toBeInTheDocument();
    expect(screen.queryByText(copy.admin.gateTitle)).not.toBeInTheDocument();
  });
});
