import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fetchMe } from "@/lib/api/auth";

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 · mock 契约承重（防再次假绿）：
// 证明 mock /auth/me.permissions **按「平台租户 / 套餐」派生**（镜像未来 BE），**绝不按 role 凭空塞**
// voice_clone_vip / analytics_view / analytics_platform。三旋钮 hd_mock_platform / hd_mock_plan / hd_mock_role。
// 线上事故态（新注册普通用户 = 普通租户 + free + role admin）→ 三个 entitlement 都没有，这里显式钉住。

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

function asNonPlatform(plan: "huading" | "free", role: "admin" | "creator") {
  localStorage.setItem("hd_mock_platform", "0");
  localStorage.setItem("hd_mock_plan", plan);
  localStorage.setItem("hd_mock_role", role);
}

describe("mock /auth/me · entitlement 按平台租户/套餐派生（不按 role、不伪造）", () => {
  it("默认（未设旋钮）= 平台租户「华鼎AI」→ 四 entitlement 齐全（含 admin_console）+ permissions sorted（零回归）", async () => {
    const me = await fetchMe();
    expect(me.permissions).toEqual(
      expect.arrayContaining(["voice_clone_vip", "analytics_view", "analytics_platform", "admin_console"])
    );
    expect(me.permissions).toContain("tenant:admin"); // admin 角色基础权限逐字镜像 BE
    expect(me.permissions).toEqual([...me.permissions].sort()); // 对齐 BE sorted()
  });

  // 🔴 线上出事态：普通租户 + free + role=admin（自助注册 owner）→ 三个 entitlement 全无。
  it("新注册普通用户（普通租户 + free + role admin）→ 无 voice_clone_vip / analytics_view / analytics_platform / admin_console（正是绕过门禁那态）", async () => {
    asNonPlatform("free", "admin");
    const me = await fetchMe();
    expect(me.user.role).toBe("admin");
    expect(me.permissions).not.toContain("voice_clone_vip");
    expect(me.permissions).not.toContain("analytics_view");
    expect(me.permissions).not.toContain("analytics_platform");
    // 管理后台 entitlement 同样与平台租户同源——role=admin 绝不给（ADMIN-CONSOLE-UI-0001 变异哨兵）。
    expect(me.permissions).not.toContain("admin_console");
    // 角色基础权限仍在（派生只增 entitlement，不动角色权限）。
    expect(me.permissions).toContain("tenant:admin");
  });

  it("VIP 客户（普通租户 + huading）→ 有 voice_clone_vip + analytics_view，但**无** analytics_platform / admin_console（不得看全站/进后台）", async () => {
    asNonPlatform("huading", "admin");
    const me = await fetchMe();
    expect(me.permissions).toContain("voice_clone_vip");
    expect(me.permissions).toContain("analytics_view");
    expect(me.permissions).not.toContain("analytics_platform");
    expect(me.permissions).not.toContain("admin_console"); // 付费 ≠ 平台方，后台仍不可进
  });

  it("VIP 客户可为 creator（付费但角色 creator）→ 同样 voice_clone_vip + analytics_view（不因 role 误伤）", async () => {
    asNonPlatform("huading", "creator");
    const me = await fetchMe();
    expect(me.user.role).toBe("creator");
    expect(me.permissions).toContain("voice_clone_vip");
    expect(me.permissions).toContain("analytics_view");
    expect(me.permissions).not.toContain("analytics_platform");
    // creator 角色基础权限仅 video:create，无 admin 专属项。
    expect(me.permissions).toContain("video:create");
    expect(me.permissions).not.toContain("tenant:admin");
  });

  it("平台租户（hd_mock_platform=1）无视套餐 → 三 entitlement 齐全（华鼎AI 全站视图）", async () => {
    localStorage.setItem("hd_mock_platform", "1");
    localStorage.setItem("hd_mock_plan", "free");
    const me = await fetchMe();
    expect(me.permissions).toEqual(expect.arrayContaining(["voice_clone_vip", "analytics_view", "analytics_platform"]));
  });

  it("entitlement 由 huadingAccess 派生非写死：free→huading 仅增 voice_clone_vip + analytics_view，基础权限不变", async () => {
    asNonPlatform("free", "creator");
    const free = await fetchMe();
    expect(free.permissions).not.toContain("voice_clone_vip");
    asNonPlatform("huading", "creator");
    const paid = await fetchMe();
    expect(new Set(paid.permissions)).toEqual(new Set([...free.permissions, "voice_clone_vip", "analytics_view"]));
  });
});
