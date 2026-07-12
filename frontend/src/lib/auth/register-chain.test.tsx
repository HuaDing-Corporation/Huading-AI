import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth/auth-context";
import { canUseVipVoiceClone, canViewAnalytics, canViewPlatformAnalytics } from "@/lib/auth/vip";
import { authStore } from "@/lib/auth/store";

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 · FIX1 整链承重（Codex B 打回的核心）：
// **不 mock @/lib/api/auth**——走真实注册请求打真 MSW（register-tenant → token 落地 → /auth/me），
// 断言 /me 反映刚注册的租户、三 entitlement 全无、由此推出 UI（升级版卡置灰 + 看板友好页）。
// **不允许**靠手工写 localStorage 旋钮构造此态——beforeEach 清空，态全由真实注册链驱动。
beforeEach(() => {
  localStorage.clear();
  authStore.clear();
});
afterEach(() => {
  localStorage.clear();
  authStore.clear();
});

function Harness() {
  const { session, ready, register } = useAuth();
  const perms = session?.user?.permissions ?? [];
  return (
    <div>
      <button
        onClick={() =>
          register({ tenantSlug: "acme", tenantName: "Acme 电商", email: "owner@acme.test", password: "pw123456" })
        }
      >
        注册
      </button>
      <span data-testid="ready">{String(ready)}</span>
      <span data-testid="tenant">{session?.user?.tenant?.id ?? "none"}</span>
      <span data-testid="email">{session?.user?.user?.email ?? "none"}</span>
      <span data-testid="role">{session?.role ?? "none"}</span>
      <span data-testid="vip">{String(canUseVipVoiceClone(session))}</span>
      <span data-testid="analytics">{String(canViewAnalytics(session))}</span>
      <span data-testid="platform">{String(canViewPlatformAnalytics(session))}</span>
      <span data-testid="perms">{perms.join(",")}</span>
    </div>
  );
}

describe("真实注册链（register → token 落地 → /auth/me）· P0 新注册态不假绿", () => {
  it("注册成功 → /me 反映刚注册的非平台 free 租户，三 entitlement 全无 → VIP 卡置灰 + 数据看板友好页", async () => {
    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("ready").textContent).toBe("true"));
    // 触发真实注册链（不预设任何旋钮）。
    fireEvent.click(screen.getByRole("button", { name: "注册" }));

    // token 落地 + fetchMe 完成 → 身份是刚注册的 ten-new（不再是 ten-mock），owner=ADMIN（正是坑）。
    await waitFor(() => expect(screen.getByTestId("tenant").textContent).toBe("ten-new"));
    expect(screen.getByTestId("email").textContent).toBe("owner@acme.test");
    expect(screen.getByTestId("role").textContent).toBe("admin");

    // 🔴 三 entitlement 全无 → 由此推出的 UI 门禁。
    expect(screen.getByTestId("vip").textContent).toBe("false"); // 升级版 VIP 卡置灰
    expect(screen.getByTestId("analytics").textContent).toBe("false"); // 数据看板 → 友好页
    expect(screen.getByTestId("platform").textContent).toBe("false");
    const perms = screen.getByTestId("perms").textContent ?? "";
    expect(perms).not.toContain("voice_clone_vip");
    expect(perms).not.toContain("analytics_view");
    expect(perms).not.toContain("analytics_platform");

    // 态由真实注册链驱动（register handler 写入），非手工旋钮。
    expect(localStorage.getItem("hd_mock_registered")).toBeTruthy();
  });
});
