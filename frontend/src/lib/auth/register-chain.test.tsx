import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/lib/auth/auth-context";
import { canUseVipVoiceClone, canViewAnalytics, canViewPlatformAnalytics } from "@/lib/auth/vip";
import { authStore } from "@/lib/auth/store";
import { AnalyticsDashboard } from "@/components/analytics/analytics-dashboard";
import { BrandVoiceCreate } from "@/components/brand-voice/brand-voice-create";
import { copy } from "@/lib/copy";

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 · FIX2 整链承重（Codex B 二次打回的核心）：
// **不 mock @/lib/api/auth / analytics hooks**——走真实注册请求打真 MSW（register-tenant → token 落地 →
// /auth/me + 真请求 /admin/analytics）。除辅助函数外，**渲染级/请求级**钉住真实行为：
//   ① 真渲染 <AnalyticsDashboard/> → analytics 真 403 ANALYTICS_PLAN_REQUIRED → 友好页可见、无任何他租户名/用户排行；
//   ② 真渲染 <BrandVoiceCreate/> → 「升级版 VIP」卡确实置灰 + 提示「开通 huading plan 后可创建」。
// 单一状态源（resolveMockState）：/me 身份+权限 与 analytics 门禁/scope 同源；注册只写 hd_mock_registered，
// 由它权威推导「非平台 free」。禁止手工写旋钮构造此态——beforeEach 清空，全由真实注册链驱动。
// 只 stub 与门禁无关的 useCreateBrandVoice（保留 useAnalytics* 真打 MSW）。
vi.mock("@/lib/api/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/hooks")>()),
  useCreateBrandVoice: () => ({ mutateAsync: vi.fn(), isPending: false })
}));

const REGISTER = { tenantSlug: "acme", tenantName: "Acme 电商", email: "owner@acme.test", password: "pw123456" };

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

// 捕获 useAuth 以便在测试里于 act(...) 内**完整 await** register 链（landToken→fetchMe→update），杜绝 act 警告。
type Auth = ReturnType<typeof useAuth>;
function Flow({ withUi, capture }: { withUi?: boolean; capture: (a: Auth) => void }) {
  const auth = useAuth();
  useEffect(() => {
    capture(auth);
  }, [auth, capture]);
  const { session } = auth;
  const perms = session?.user?.permissions ?? [];
  return (
    <div>
      <span data-testid="ready">{String(auth.ready)}</span>
      <span data-testid="tenant">{session?.user?.tenant?.id ?? "none"}</span>
      <span data-testid="email">{session?.user?.user?.email ?? "none"}</span>
      <span data-testid="role">{session?.role ?? "none"}</span>
      <span data-testid="vip">{String(canUseVipVoiceClone(session))}</span>
      <span data-testid="analytics">{String(canViewAnalytics(session))}</span>
      <span data-testid="platform">{String(canViewPlatformAnalytics(session))}</span>
      <span data-testid="perms">{perms.join(",")}</span>
      {withUi && session && (
        <>
          <AnalyticsDashboard />
          <BrandVoiceCreate />
        </>
      )}
    </div>
  );
}

async function renderAndRegister(withUi = false) {
  let auth: Auth | null = null;
  render(
    <QueryClientProvider client={makeClient()}>
      <AuthProvider>
        <Flow withUi={withUi} capture={(a) => (auth = a)} />
      </AuthProvider>
    </QueryClientProvider>
  );
  await waitFor(() => expect(auth?.ready).toBe(true));
  // 真实注册链，完整 await 于 act 内（不预设任何旋钮）→ 所有 AuthProvider 状态更新被吸收，无 act 警告。
  await act(async () => {
    await auth!.register(REGISTER);
  });
  await waitFor(() => expect(screen.getByTestId("tenant").textContent).toBe("ten-new"));
}

beforeEach(() => {
  // 只在 beforeEach 复位 authStore：此刻上个测试的组件已被 RTL cleanup 卸载（订阅已解绑），
  // clear() 不会对仍挂载的 AuthProvider 发状态更新 → 无 act 警告。afterEach 不碰 authStore（避免在卸载前触发更新）。
  localStorage.clear();
  authStore.clear();
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("真实注册链（register → token 落地 → /me + analytics）· P0 新注册态不假绿", () => {
  it("注册成功 → /me 反映刚注册的非平台 free 租户，三 entitlement 全无（身份 + 辅助函数）", async () => {
    await renderAndRegister(false);
    expect(screen.getByTestId("email").textContent).toBe("owner@acme.test");
    expect(screen.getByTestId("role").textContent).toBe("admin"); // self-serve owner = ADMIN（正是坑）
    expect(screen.getByTestId("vip").textContent).toBe("false");
    expect(screen.getByTestId("analytics").textContent).toBe("false");
    expect(screen.getByTestId("platform").textContent).toBe("false");
    const perms = screen.getByTestId("perms").textContent ?? "";
    expect(perms).not.toContain("voice_clone_vip");
    expect(perms).not.toContain("analytics_view");
    expect(perms).not.toContain("analytics_platform");
    // 态由真实注册链驱动（register handler 只写这一个 key），非手工旋钮。
    expect(localStorage.getItem("hd_mock_registered")).toBeTruthy();
  });

  it("注册成功 → 渲染/请求级承重：数据看板真 403 友好页 + 无他租户名 + 「升级版 VIP」卡置灰", async () => {
    await renderAndRegister(true);
    // ① 数据看板：真请求 /admin/analytics → 403 ANALYTICS_PLAN_REQUIRED → 友好页；不出现区间选择器/用户排行/他租户名。
    expect(await screen.findByText(copy.analytics.planRequiredTitle)).toBeInTheDocument();
    expect(screen.queryByText(copy.analytics.rangeLabel)).not.toBeInTheDocument();
    expect(screen.queryByText(copy.analytics.tenantTitle)).not.toBeInTheDocument();
    expect(screen.queryByText(/^用户 \d+$/)).not.toBeInTheDocument();
    // ② 品牌音色创建卡：「升级版 VIP」(doubao) 置灰 + 「开通 huading plan 后可创建」（区别于「暂无可用音色槽位」）。
    expect(screen.getByText(copy.brandVoice.providerVipLocked)).toBeInTheDocument();
    expect(screen.queryByText(copy.brandVoice.providerDoubaoDesc)).not.toBeInTheDocument();
  });
});
