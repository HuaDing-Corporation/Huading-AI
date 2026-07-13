import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  fetchAnalyticsByProvider,
  fetchAnalyticsByTenant,
  fetchAnalyticsOverview,
  fetchAnalyticsTimeseries,
  type AnalyticsRange
} from "@/lib/api/analytics";

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 · FIX3 · VIP 客户（非平台 + huading）analytics scope 承重（防泄漏第二层）：
// 此态由「运营给客户开通 huading」产生、**不能由注册链产生**（注册必然 free 且被 guard 403 拦住，走不到 scope）——
// 故允许旋钮构造，但断言全为**请求级 / 数据级**（真打 MSW，不 mock analytics hooks），证明各端点**只返本租户合计、绝不返全站聚合**。
// ⚠️ fixture 成败关键：本租户（OWN_TENANT，1 个，overview credits 1280.5）与全站（46 个租户，overview 合计
//    48213.5 = 常量 PLATFORM_TOTAL_CREDITS）**显著不同**，否则 scope 变异不会变红。下面用「平台方对照」证明二者必然不同（非全 1 租户假过）。
const range: AnalyticsRange = { from: "2026-06-01", to: "2026-06-30" };
const byTenantOpts = { sort: "credits_desc" as const, limit: 100, offset: 0 };

const OWN_CREDITS = 1280.5; // OWN_TENANT.credits_used（VIP overview.total_credits_used）
const FULL_CREDITS = 48213.5; // 平台方 overview.total_credits_used（= 常量 PLATFORM_TOTAL_CREDITS / by-provider 之和；非 by-tenant 列表原始和）
const FULL_TENANTS = 46;

function asVipCustomer() {
  localStorage.setItem("hd_mock_platform", "0"); // 非平台租户
  localStorage.setItem("hd_mock_plan", "huading"); // 运营已开通 huading
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("VIP 客户（非平台 huading）· analytics 各端点只返本租户，不泄全站（scope 双保险承重）", () => {
  it("/by-tenant：total=1，items 只含本租户（OWN），不含任何其它租户 id/name", async () => {
    asVipCustomer();
    const res = await fetchAnalyticsByTenant(range, byTenantOpts);
    expect(res.total).toBe(1);
    expect(res.items).toHaveLength(1);
    expect(res.items[0].tenant_id).toBe("ten-mock");
    // 绝不出现全站租户（ten-0xx / 「用户 N」）——scopedTenants 变异成返全站即红。
    expect(res.items.some((t) => /^ten-0\d\d$/.test(t.tenant_id))).toBe(false);
    expect(res.items.some((t) => /^用户 \d+$/.test(t.tenant_name))).toBe(false);
  });

  it("overview：聚合 = 本租户合计（credits 1280.5、tenant_count 1），绝不等于全站（48213.5 / 46）", async () => {
    asVipCustomer();
    const ov = await fetchAnalyticsOverview(range);
    expect(ov.total_credits_used).toBe(OWN_CREDITS);
    expect(ov.tenant_count).toBe(1);
    // overview 忽略 scope 返全站聚合即红。
    expect(ov.total_credits_used).not.toBe(FULL_CREDITS);
    expect(ov.tenant_count).not.toBe(FULL_TENANTS);
  });

  it("by-provider：拆分 credits 合计为本租户量级（≈1280.5，远小于全站 48213.5）", async () => {
    asVipCustomer();
    const bp = await fetchAnalyticsByProvider(range);
    const sum = bp.items.reduce((s, i) => s + i.credits_used, 0);
    expect(sum).toBeGreaterThan(0);
    // by-provider 忽略 scope 返全站即红（own ~1280 « full 48213）。
    expect(sum).toBeLessThan(FULL_CREDITS / 10);
  });

  it("timeseries：曲线为本租户量级（首桶远小于全站首桶 ~800）", async () => {
    asVipCustomer();
    const ts = await fetchAnalyticsTimeseries(range, "day");
    expect(ts.buckets.length).toBeGreaterThan(0);
    // timeseries 忽略 scope 返全站即红（全站首桶 ~800，本租户按占比缩放 ~21）。
    expect(ts.buckets[0].credits_used).toBeLessThan(100);
  });

  it("对照·平台方：/by-tenant total=46 + overview 全站 48213.5（证明 fixture「本租户 ≠ 全站」，非全 1 租户假过）", async () => {
    localStorage.setItem("hd_mock_platform", "1");
    const bt = await fetchAnalyticsByTenant(range, byTenantOpts);
    expect(bt.total).toBe(FULL_TENANTS);
    const ov = await fetchAnalyticsOverview(range);
    expect(ov.total_credits_used).toBe(FULL_CREDITS);
    expect(ov.tenant_count).toBe(FULL_TENANTS);
  });
});
