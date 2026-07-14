import { describe, expect, it } from "vitest";

import type { Session } from "@/lib/auth/store";
import {
  canUseAdminConsole,
  canUseVipVoiceClone,
  canViewAnalytics,
  canViewPlatformAnalytics
} from "@/lib/auth/vip";

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 · 承重（门禁一律走 permissions，绝不走 role）。
// 构造最小 Session：只关心 role 与 user.permissions（其余字段对判定无关，用 as 断言收窄）。
const session = (role: "admin" | "creator", permissions: string[]): Session =>
  ({ role, user: { permissions } } as unknown as Session);

describe("canUseVipVoiceClone（升级版 VIP 门禁 · 唯一信号 = permissions）", () => {
  // 🔴 变异哨兵：把 `if (session.role === "admin") return true;` 加回 vip.ts，本条立即变红。
  // 线上事故正是自助注册 owner=role admin 但无 voice_clone_vip 却被放行。
  it("role=admin 但 permissions 无 voice_clone_vip → false（新注册 owner 不得绕过门禁）", () => {
    expect(canUseVipVoiceClone(session("admin", []))).toBe(false);
    expect(canUseVipVoiceClone(session("admin", ["tenant:admin", "video:create"]))).toBe(false);
  });

  it("permissions 含 voice_clone_vip → true（无论 role）", () => {
    expect(canUseVipVoiceClone(session("creator", ["voice_clone_vip"]))).toBe(true);
    expect(canUseVipVoiceClone(session("admin", ["voice_clone_vip"]))).toBe(true);
  });

  it("null / 无 user / 无 permissions → false（不崩、按无权处理）", () => {
    expect(canUseVipVoiceClone(null)).toBe(false);
    expect(canUseVipVoiceClone({ role: "admin" } as unknown as Session)).toBe(false);
  });
});

describe("canViewAnalytics / canViewPlatformAnalytics（只看对应 permission，不看 role）", () => {
  it("analytics_view 决定能否进看板；role=admin 无此权限 → false", () => {
    expect(canViewAnalytics(session("admin", []))).toBe(false);
    expect(canViewAnalytics(session("creator", ["analytics_view"]))).toBe(true);
  });

  it("analytics_platform 决定全站视图；VIP 客户（仅 analytics_view）→ false（不得看全站）", () => {
    expect(canViewPlatformAnalytics(session("admin", ["analytics_view"]))).toBe(false);
    expect(canViewPlatformAnalytics(session("admin", ["analytics_platform"]))).toBe(true);
  });

  // 🔴 变异哨兵（ADMIN-CONSOLE-UI-0001）：给 canUseAdminConsole 加回任何 role 快捷分支，本条立即变红。
  it("canUseAdminConsole：唯一信号 = permissions 含 admin_console；role=admin 无该权限 → false（后台不给自助注册 owner 开门）", () => {
    expect(canUseAdminConsole(session("admin", []))).toBe(false);
    expect(canUseAdminConsole(session("admin", ["tenant:admin", "voice_clone_vip", "analytics_view"]))).toBe(false);
    expect(canUseAdminConsole(session("creator", ["admin_console"]))).toBe(true);
    expect(canUseAdminConsole(null)).toBe(false);
  });

  it("三态一致性：新注册（无 entitlement）三者皆 false；VIP 有 view 无 platform；平台方三者皆真", () => {
    const fresh = session("admin", ["tenant:admin", "video:create"]);
    expect([canUseVipVoiceClone(fresh), canViewAnalytics(fresh), canViewPlatformAnalytics(fresh)]).toEqual([false, false, false]);
    const vip = session("creator", ["video:create", "voice_clone_vip", "analytics_view"]);
    expect([canUseVipVoiceClone(vip), canViewAnalytics(vip), canViewPlatformAnalytics(vip)]).toEqual([true, true, false]);
    const platform = session("admin", ["voice_clone_vip", "analytics_view", "analytics_platform"]);
    expect([canUseVipVoiceClone(platform), canViewAnalytics(platform), canViewPlatformAnalytics(platform)]).toEqual([true, true, true]);
  });
});
