import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fetchMe } from "@/lib/api/auth";

// ADMIN-VIP-GATE-UI-0001-FIX1 · mock 契约承重（Codex B P1）：
// 证明 mock 的 /auth/me.permissions 是**按「角色 + 套餐」派生**的（镜像真实 BE VIP-ENTITLEMENT-BE-0001），
// **不是**把 voice_clone_vip 写死进某个角色。旧 mock 硬塞权限 → 组件测试/e2e 假绿、creator+huading 真付费用户被误伤。
// 这里直接打 /me（走全局 MSW），用 localStorage 双旋钮切场景，断言 voice_clone_vip 的有无与套餐一致。

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("mock /auth/me · VIP entitlement 按套餐派生（不伪造权限）", () => {
  it("默认（未设旋钮）= admin → 含 voice_clone_vip（零回归）", async () => {
    const me = await fetchMe();
    expect(me.user.role).toBe("admin");
    expect(me.permissions).toContain("voice_clone_vip");
  });

  it("承重·真实付费用户：creator + huading → 含 voice_clone_vip（旧 mock 会漏，正是被误伤的那类）", async () => {
    localStorage.setItem("hd_mock_role", "creator");
    localStorage.setItem("hd_mock_plan", "huading");
    const me = await fetchMe();
    expect(me.user.role).toBe("creator");
    expect(me.permissions).toContain("voice_clone_vip");
  });

  it("creator + free → 不含 voice_clone_vip（无套餐即无 entitlement）", async () => {
    localStorage.setItem("hd_mock_role", "creator");
    localStorage.setItem("hd_mock_plan", "free");
    const me = await fetchMe();
    expect(me.user.role).toBe("creator");
    expect(me.permissions).not.toContain("voice_clone_vip");
  });

  it("admin + free → 仍含 voice_clone_vip（admin 无视套餐，与 BE「admin OR plan=huading」一致）", async () => {
    localStorage.setItem("hd_mock_role", "admin");
    localStorage.setItem("hd_mock_plan", "free");
    const me = await fetchMe();
    expect(me.permissions).toContain("voice_clone_vip");
  });

  it("voice_clone_vip 非角色写死：基础权限恒含 video:create，voice_clone_vip 仅随 entitlement 增减", async () => {
    localStorage.setItem("hd_mock_role", "creator");
    localStorage.setItem("hd_mock_plan", "free");
    const free = await fetchMe();
    expect(free.permissions).toContain("video:create");
    localStorage.setItem("hd_mock_plan", "huading");
    const paid = await fetchMe();
    // 仅 voice_clone_vip 因套餐变化而增加，基础权限不变。
    expect(paid.permissions).toEqual([...free.permissions, "voice_clone_vip"]);
  });
});
