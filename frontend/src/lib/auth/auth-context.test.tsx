import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CurrentUserResponse } from "@/lib/api/types";

// ADMIN-VIP-GATE-UI-0001-FIX1 · entitlement 刷新承重（Codex B P1 第二点）：
// localStorage 里的 user/permissions 只是缓存；运营开通 huading 后，用户**刷新页面**应能拿到新权限，
// 不能永久吃旧 session。此测证明 AuthProvider mount 时会 fetchMe 覆盖 user（已登录才拉；未登录不打 /me）。
const authApi = vi.hoisted(() => ({ fetchMe: vi.fn() }));
vi.mock("@/lib/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/auth")>()),
  fetchMe: authApi.fetchMe
}));

import { authStore } from "@/lib/auth/store";
import { AuthProvider, useAuth } from "./auth-context";

const me = (permissions: string[], id = "u-mock", email = "qa@huading.test"): CurrentUserResponse => ({
  tenant: { id: "ten-mock", slug: "huading", name: "华鼎（mock）" },
  user: { id, tenant_id: "ten-mock", email, full_name: "QA", role: "creator" },
  permissions
});

function Probe() {
  const { session, ready } = useAuth();
  return (
    <span data-testid="perms">{ready ? (session?.user?.permissions?.join(",") ?? "signed-out") : "loading"}</span>
  );
}

beforeEach(() => {
  localStorage.clear();
  authStore.clear(); // 复位单例（session=null, hydrated）
  authApi.fetchMe.mockReset();
});
afterEach(() => vi.clearAllMocks());

describe("AuthProvider · mount 刷新 entitlement", () => {
  it("已登录但 permissions 陈旧 → mount fetchMe 覆盖 → 刷新后拿到 voice_clone_vip（开通即时生效）", async () => {
    // 预置「开通 huading 前登录」的缓存会话：无 voice_clone_vip。
    authStore.set({
      token: "t",
      tenantId: "ten-mock",
      userId: "u-mock",
      role: "creator",
      user: me(["video:create"])
    });
    // 运营已开通 → /me 现返回含 voice_clone_vip。
    authApi.fetchMe.mockResolvedValue(me(["video:create", "voice_clone_vip"]));

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("perms").textContent).toContain("voice_clone_vip"));
    expect(authApi.fetchMe).toHaveBeenCalledTimes(1);
  });

  it("未登录（无 token）→ 不打 /me（/login 页不无谓拉 me）", async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("perms").textContent).toBe("signed-out"));
    expect(authApi.fetchMe).not.toHaveBeenCalled();
  });

  it("已登录但 /me 失败 → 静默保留旧缓存（离线/瞬断不误登出）", async () => {
    authStore.set({
      token: "t",
      tenantId: "ten-mock",
      userId: "u-mock",
      role: "creator",
      user: me(["video:create"])
    });
    authApi.fetchMe.mockRejectedValue(new Error("network"));

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(authApi.fetchMe).toHaveBeenCalledTimes(1));
    // 仍在登录态，权限保留旧缓存（未被清空、未登出）。
    expect(screen.getByTestId("perms").textContent).toBe("video:create");
  });

  // 承重·竞态守卫（对抗评审确证项）：刷新未落定时切到另一账号 → 旧 /me 晚到不得污染新会话（跨账号）。
  it("竞态守卫：刷新期间已改登另一账号 → 旧账号 /me 晚到不覆盖新会话（不跨账号污染）", async () => {
    let resolveMe!: (v: CurrentUserResponse) => void;
    authApi.fetchMe.mockReturnValue(new Promise<CurrentUserResponse>((r) => (resolveMe = r)));
    // 账号 A 已登录（缓存无 vip），mount 发起 fetchMe(A)。
    authStore.set({ token: "tA", tenantId: "ten-A", userId: "u-A", role: "creator", user: me(["video:create"], "u-A", "a@huading.test") });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(authApi.fetchMe).toHaveBeenCalledTimes(1));

    // 刷新未落定 → 登出并改登账号 B（不同 userId，B 有自己的 vip 权限/身份）。
    authStore.set({ token: "tB", tenantId: "ten-B", userId: "u-B", role: "admin", user: me(["video:create", "voice_clone_vip"], "u-B", "b@huading.test") });
    // 旧 /me（账号 A）此刻才 resolve。
    resolveMe(me(["video:create"], "u-A", "a@huading.test"));
    await Promise.resolve();
    await Promise.resolve();

    // B 的会话未被 A 覆盖：身份仍是 B、权限仍是 B 的。
    const s = authStore.get();
    expect(s?.userId).toBe("u-B");
    expect(s?.user?.user.email).toBe("b@huading.test");
    expect(s?.user?.permissions).toContain("voice_clone_vip");
  });
});
