import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  fetchAnalyticsByProvider,
  fetchAnalyticsByTenant,
  fetchAnalyticsOverview,
  fetchAnalyticsTimeseries,
  type AnalyticsRange
} from "@/lib/api/analytics";

// PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001 · FIX4 · VIP 客户（非平台 + huading）analytics scope 承重（防泄漏第二层 · 确定值）：
// 此态由「运营给客户开通 huading」产生、**不能由注册链产生**（注册必然 free 且被 guard 403 拦，走不到 scope）——
// 故允许旋钮构造，但断言全为**请求级 + 逐字段确定值（toBe）**（真打 MSW，不 mock analytics hooks）。
// ⚠️ **全文件不允许「量级型」范围断言（<、>、toBeLessThan…）承担防泄漏职责**——它们会放过「部分泄漏 / 按错比例缩放 /
//    只泄漏 cost·task」。期望值全部从确定性 fixture 逐字节推导（镜像 handlers.ts 的缩放公式）。
// fixture：本租户 credits 1280.5（1 租户）与全站 48213.5（46 租户）显著不同；末尾「平台方对照」用例证明本租户值 ≠ 全站值是**结构性**成立。
const range: AnalyticsRange = { from: "2026-06-01", to: "2026-06-30" };
const byTenantOpts = { sort: "credits_desc" as const, limit: 100, offset: 0 };

// OWN_TENANT fixture（= handlers.ts OWN_TENANT）与全站合计常量。
const OWN_CREDITS = 1280.5;
const OWN_COST = 48200;
const OWN_TASK = 96;
const FULL_CREDITS = 48213.5; // = 常量 PLATFORM_TOTAL_CREDITS（平台方 overview.total_credits_used / by-provider 缩放分母；非 by-tenant 列表原始和）
const FULL_TENANTS = 46;

// 全站 by-provider 原始 fixture（逐字节镜像 handlers.ts:441-444）——平台方即此值；VIP 按 s 缩放。
const PROVIDER_FULL = [
  { provider: "seedance", credits_used: 21500, cost_cents: 812000, task_count: 2100 },
  { provider: "video_gen", credits_used: 15200, cost_cents: 540300, task_count: 1630 },
  { provider: "copywriting", credits_used: 6800, cost_cents: 210400, task_count: 980 },
  { provider: "image", credits_used: 4713.5, cost_cents: 329640, task_count: 520 }
];
// 全站 timeseries 第 i 桶原始值（逐字节镜像 handlers.ts:465-467）。
const tsBase = (i: number) => ({
  credits_used: 800 + Math.sin(i) * 300 + i * 40,
  cost_cents: 30000 + i * 4200 + (i % 3) * 1500,
  task_count: 120 + i * 9 + (i % 4) * 15
});
// 缩放规则逐字节镜像 handlers.ts：credits 保 1 位小数、cost/task 取整。s=平台 1 / VIP=OWN/FULL。
const scaleCredits = (n: number, s: number) => Math.round(n * s * 10) / 10;
const scaleInt = (n: number, s: number) => Math.round(n * s);
const S_VIP = OWN_CREDITS / FULL_CREDITS; // = handlers.ts 的 s（VIP）
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

function asVipCustomer() {
  localStorage.setItem("hd_mock_platform", "0"); // 非平台租户
  localStorage.setItem("hd_mock_plan", "huading"); // 运营已开通 huading
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("VIP 客户（非平台 huading）· analytics 各端点逐字段确定值 = 本租户，绝不泄全站（scope 承重）", () => {
  it("/by-tenant：total=1，items 只含本租户（OWN），不含任何其它租户 id/name（含偏序泄漏）", async () => {
    asVipCustomer();
    const res = await fetchAnalyticsByTenant(range, byTenantOpts);
    expect(res.total).toBe(1);
    expect(res.items).toHaveLength(1);
    expect(res.items[0].tenant_id).toBe("ten-mock");
    // 绝不出现全站租户（ten-0xx / 「用户 N」）——全量或偏序泄漏即红。
    expect(res.items.some((t) => /^ten-0\d\d$/.test(t.tenant_id))).toBe(false);
    expect(res.items.some((t) => /^用户 \d+$/.test(t.tenant_name))).toBe(false);
  });

  it("overview：逐字段 = 本租户合计（credits 1280.5 / cost 48200 / task 96 / tenant_count 1），确定值", async () => {
    asVipCustomer();
    const ov = await fetchAnalyticsOverview(range);
    expect(ov.total_credits_used).toBe(OWN_CREDITS);
    expect(ov.total_cost_cents).toBe(OWN_COST);
    expect(ov.task_count).toBe(OWN_TASK);
    expect(ov.tenant_count).toBe(1);
  });

  it("by-provider：每行 credits_used / cost_cents / task_count + 三项合计，全部 = 本租户缩放确定值（toBe）", async () => {
    asVipCustomer();
    const bp = await fetchAnalyticsByProvider(range);
    expect(bp.items).toHaveLength(PROVIDER_FULL.length);
    bp.items.forEach((row, i) => {
      const raw = PROVIDER_FULL[i];
      expect(row.provider).toBe(raw.provider);
      expect(row.credits_used).toBe(scaleCredits(raw.credits_used, S_VIP));
      expect(row.cost_cents).toBe(scaleInt(raw.cost_cents, S_VIP)); // cost 泄漏（取消缩放）即红
      expect(row.task_count).toBe(scaleInt(raw.task_count, S_VIP)); // task 泄漏即红
    });
    // 三项合计（与响应同序同运算 → 精确）。
    expect(sum(bp.items.map((r) => r.credits_used))).toBe(sum(PROVIDER_FULL.map((r) => scaleCredits(r.credits_used, S_VIP))));
    expect(sum(bp.items.map((r) => r.cost_cents))).toBe(sum(PROVIDER_FULL.map((r) => scaleInt(r.cost_cents, S_VIP))));
    expect(sum(bp.items.map((r) => r.task_count))).toBe(sum(PROVIDER_FULL.map((r) => scaleInt(r.task_count, S_VIP))));
  });

  it("timeseries（日 14 桶）：首桶 + 末桶 + 三项合计，全部 = 本租户缩放确定值（toBe）", async () => {
    asVipCustomer();
    const ts = await fetchAnalyticsTimeseries(range, "day");
    expect(ts.buckets).toHaveLength(14);
    for (const idx of [0, 13]) {
      const raw = tsBase(idx);
      const b = ts.buckets[idx];
      expect(b.credits_used).toBe(scaleCredits(raw.credits_used, S_VIP));
      expect(b.cost_cents).toBe(scaleInt(raw.cost_cents, S_VIP));
      expect(b.task_count).toBe(scaleInt(raw.task_count, S_VIP));
    }
    const idxs = Array.from({ length: 14 }, (_, i) => i);
    expect(sum(ts.buckets.map((b) => b.credits_used))).toBe(sum(idxs.map((i) => scaleCredits(tsBase(i).credits_used, S_VIP))));
    expect(sum(ts.buckets.map((b) => b.cost_cents))).toBe(sum(idxs.map((i) => scaleInt(tsBase(i).cost_cents, S_VIP))));
    expect(sum(ts.buckets.map((b) => b.task_count))).toBe(sum(idxs.map((i) => scaleInt(tsBase(i).task_count, S_VIP))));
  });

  it("timeseries（周 6 桶）：首桶 + 末桶 + 三项合计，全部 = 本租户缩放确定值（toBe）", async () => {
    asVipCustomer();
    const ts = await fetchAnalyticsTimeseries(range, "week");
    expect(ts.buckets).toHaveLength(6);
    for (const idx of [0, 5]) {
      const raw = tsBase(idx);
      const b = ts.buckets[idx];
      expect(b.credits_used).toBe(scaleCredits(raw.credits_used, S_VIP));
      expect(b.cost_cents).toBe(scaleInt(raw.cost_cents, S_VIP));
      expect(b.task_count).toBe(scaleInt(raw.task_count, S_VIP));
    }
    const idxs = Array.from({ length: 6 }, (_, i) => i);
    expect(sum(ts.buckets.map((b) => b.credits_used))).toBe(sum(idxs.map((i) => scaleCredits(tsBase(i).credits_used, S_VIP))));
    expect(sum(ts.buckets.map((b) => b.cost_cents))).toBe(sum(idxs.map((i) => scaleInt(tsBase(i).cost_cents, S_VIP))));
    expect(sum(ts.buckets.map((b) => b.task_count))).toBe(sum(idxs.map((i) => scaleInt(tsBase(i).task_count, S_VIP))));
  });

  it("对照·平台方（s=1）：同样字段 = **全站**确定值（证明本租户值 ≠ 全站值是结构性成立，非碰巧）", async () => {
    localStorage.setItem("hd_mock_platform", "1");
    // by-tenant：46 租户
    const bt = await fetchAnalyticsByTenant(range, byTenantOpts);
    expect(bt.total).toBe(FULL_TENANTS);
    // overview：全站合计
    const ov = await fetchAnalyticsOverview(range);
    expect(ov.total_credits_used).toBe(FULL_CREDITS);
    expect(ov.total_cost_cents).toBe(1892340);
    expect(ov.task_count).toBe(5230);
    expect(ov.tenant_count).toBe(FULL_TENANTS);
    // by-provider：s=1 → 全站原始值（逐字段）。
    const bp = await fetchAnalyticsByProvider(range);
    bp.items.forEach((row, i) => {
      expect(row.credits_used).toBe(PROVIDER_FULL[i].credits_used);
      expect(row.cost_cents).toBe(PROVIDER_FULL[i].cost_cents);
      expect(row.task_count).toBe(PROVIDER_FULL[i].task_count);
    });
    // timeseries：s=1 → 全站原始首桶（与 VIP 缩放值必然不同：credits 800 vs ~21）。
    const ts = await fetchAnalyticsTimeseries(range, "day");
    expect(ts.buckets[0].credits_used).toBe(scaleCredits(tsBase(0).credits_used, 1));
    expect(ts.buckets[0].cost_cents).toBe(scaleInt(tsBase(0).cost_cents, 1));
    expect(ts.buckets[0].task_count).toBe(scaleInt(tsBase(0).task_count, 1));
    // 结构性证明：本租户缩放值 ≠ 全站原始值（同一字段）。
    expect(scaleCredits(PROVIDER_FULL[0].credits_used, S_VIP)).not.toBe(PROVIDER_FULL[0].credits_used);
    expect(scaleInt(PROVIDER_FULL[0].cost_cents, S_VIP)).not.toBe(PROVIDER_FULL[0].cost_cents);
    expect(scaleInt(PROVIDER_FULL[0].task_count, S_VIP)).not.toBe(PROVIDER_FULL[0].task_count);
  });
});
