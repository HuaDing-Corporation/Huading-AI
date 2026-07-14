import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  adjustTenantCredits,
  assignVoiceSlot,
  changeTenantStatus,
  exportAdminUsageCsv,
  fetchAdminAudit,
  fetchAdminTasks,
  fetchAdminTenantDetail,
  fetchAdminTenants,
  fetchAdminUsage,
  fetchAdminVoiceSlots,
  retryAdminTask
} from "@/lib/api/admin-console";
import { ApiError } from "@/lib/api/client";

// 管理员后台 mock 契约承重（ADMIN-CONSOLE-UI-0001）：真打 MSW（不 mock adapter/hooks），断言全确定值（toBe）——
// 禁止量级/范围断言承担安全职责（P0 血的教训）。门禁与 /me 的 admin_console 同源（resolveMockState）。
// ⚠️ 本文件 mock 数据为模块级内存态：只读用例在前、写操作在后（写会改余额/审计，顺序即契约）。

const LIST = { sort: "created_desc" as const, limit: 20, offset: 0 };

function asNonPlatform() {
  localStorage.setItem("hd_mock_platform", "0");
  localStorage.setItem("hd_mock_plan", "free");
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("门禁：非平台（新注册态）→ 全端点 403 PLATFORM_ADMIN_REQUIRED（与 /me admin_console 同源）", () => {
  it("tenants / voice-slots / usage / tasks / audit / 写操作 全部 403，不泄任何数据", async () => {
    asNonPlatform();
    const expect403 = async (p: Promise<unknown>) => {
      const err = await p.catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(403);
      expect((err as ApiError).code).toBe("PLATFORM_ADMIN_REQUIRED");
    };
    await expect403(fetchAdminTenants(LIST));
    await expect403(fetchAdminTenantDetail("ten-acme"));
    await expect403(fetchAdminVoiceSlots());
    await expect403(fetchAdminUsage({ limit: 20, offset: 0 }));
    await expect403(fetchAdminTasks({ limit: 20, offset: 0 }));
    await expect403(fetchAdminAudit({ limit: 20, offset: 0 }));
    await expect403(adjustTenantCredits("ten-acme", { delta: 100, reason: "x" }));
    await expect403(retryAdminTask("task-f1"));
    await expect403(exportAdminUsageCsv({}));
  });
});

describe("平台账号（默认）· 读端点确定值", () => {
  it("tenants：total=4，created_desc 首位 beta；搜索 acme → 恰 1 条；套餐 huading → 恰 2 条", async () => {
    const all = await fetchAdminTenants(LIST);
    expect(all.total).toBe(4);
    expect(all.items[0].slug).toBe("beta"); // 2026-07-01 最新
    expect(all.items[0].owner_email).toBe("ops@beta.test");
    const searched = await fetchAdminTenants({ ...LIST, search: "acme" });
    expect(searched.total).toBe(1);
    expect(searched.items[0].slug).toBe("acme");
    expect(searched.items[0].balance).toEqual({ total: 20000, used: 5000, reserved: 1000, remaining: 14000 });
    const huading = await fetchAdminTenants({ ...LIST, plan: "huading" });
    expect(huading.total).toBe(2); // huading（平台自己）+ acme
  });

  it("tenant 详情：acme 基础信息 + 最近任务/用量/槽位 确定值", async () => {
    const d = await fetchAdminTenantDetail("ten-acme");
    expect(d.tenant.slug).toBe("acme");
    expect(d.tenant.is_platform).toBe(false);
    expect(d.recent_tasks.map((t) => t.id)).toEqual(["task-f1", "task-r1", "task-q1"]);
    expect(d.recent_usage).toHaveLength(4); // u-1/u-2/u-4/u-6
    expect(d.voice_slots).toEqual([{ speaker_id: "S_acme_001", voice_name: "我的主播音" }]);
  });

  it("voice-slots：平台池 3 个（1 占用 2 空闲）+ 租户槽位恰 1 条", async () => {
    const s = await fetchAdminVoiceSlots();
    expect(s.platform_pool).toHaveLength(3);
    expect(s.platform_pool[0]).toEqual({ speaker_id: "S_pool_001", occupied_by: { tenant_slug: "acme", voice_name: "我的主播音" } });
    expect(s.platform_pool.filter((p) => p.occupied_by === null)).toHaveLength(2);
    expect(s.tenant_slots).toEqual([{ tenant_slug: "acme", speaker_id: "S_acme_001", voice_name: "我的主播音" }]);
  });

  it("usage：全量 6 条；按租户 beta 筛 → 恰 1 条（u-3, 10 积分）；状态 released → 恰 1 条", async () => {
    const all = await fetchAdminUsage({ limit: 20, offset: 0 });
    expect(all.total).toBe(6);
    const beta = await fetchAdminUsage({ tenant_id: "ten-beta", limit: 20, offset: 0 });
    expect(beta.total).toBe(1);
    expect(beta.items[0]).toMatchObject({ id: "u-3", tenant_slug: "beta", credits: 10, cost_cents: 30 });
    const released = await fetchAdminUsage({ status: "released", limit: 20, offset: 0 });
    expect(released.total).toBe(1);
    expect(released.items[0].id).toBe("u-4");
  });

  it("tasks：全量 6；status=failed → 恰 3 条（created_at 倒序 f3/f2/f1）且错误码/信息确定值", async () => {
    const all = await fetchAdminTasks({ limit: 20, offset: 0 });
    expect(all.total).toBe(6);
    const failed = await fetchAdminTasks({ status: "failed", limit: 20, offset: 0 });
    expect(failed.total).toBe(3);
    expect(failed.items[0]).toMatchObject({ id: "task-f3", error_code: "PROVIDER_ERROR" });
    expect(failed.items[1]).toMatchObject({ id: "task-f2", error_code: "tenant_quota_exceeded", error_message: "额度不足，任务未启动" });
    expect(failed.items[2]).toMatchObject({ id: "task-f1", error_code: "PROVIDER_TIMEOUT" });
  });

  it("CSV 导出：无筛选 6 行 > 上限 3 → 422 中文（原样展示）；按 beta 筛 1 行 → 200 CSV 表头+1 数据行", async () => {
    const err = await exportAdminUsageCsv({}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("EXPORT_LIMIT_EXCEEDED");
    expect((err as ApiError).message).toBe("导出行数（6）超出上限（3），请缩小筛选范围");
    const blob = await exportAdminUsageCsv({ tenant_id: "ten-beta" });
    // jsdom Blob 无 .text()、Response 也读不了 → 用 FileReader（jsdom 原生实现）。
    const text = await new Promise<string>((resolve) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.readAsText(blob);
    });
    const lines = text.split("\n");
    expect(lines).toHaveLength(2); // 表头 + 1 行
    expect(lines[1].startsWith("2026-07-11T09:02:00Z,beta,copywriting,deepseek,v3,1,篇,10,30,settled")).toBe(true);
  });
});

describe("平台账号 · 写操作（顺序即契约：写会改内存态）", () => {
  it("余额 +5000（acme）：total 20000→25000、remaining 14000→19000；审计新增 credits_adjust（前→后 + 理由）", async () => {
    const res = await adjustTenantCredits("ten-acme", { delta: 5000, reason: "线下打款充值" });
    expect(res.balance).toEqual({ total: 25000, used: 5000, reserved: 1000, remaining: 19000 });
    const audit = await fetchAdminAudit({ action: "credits_adjust", limit: 20, offset: 0 });
    expect(audit.total).toBe(1);
    expect(audit.items[0]).toMatchObject({
      action: "credits_adjust",
      target_tenant_slug: "acme",
      before: { quota_credits_total: 20000 },
      after: { quota_credits_total: 25000 },
      reason: "线下打款充值"
    });
  });

  it("下限保护：gamma（total 5000 / 已用 4200 + 预留 600）扣 -300 → 422 中文 message 确定值，余额不变", async () => {
    const err = await adjustTenantCredits("ten-gamma", { delta: -300, reason: "回收" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("CREDITS_BELOW_COMMITTED");
    expect((err as ApiError).message).toBe("扣减后额度（4700）会低于已用+预留（4800），已拒绝");
    const detail = await fetchAdminTenantDetail("ten-gamma");
    expect(detail.tenant.balance.total).toBe(5000); // 拒绝后分文未动
  });

  it("停用平台租户自己（ten-mock）→ 422 PLATFORM_TENANT_PROTECTED，状态不变", async () => {
    const err = await changeTenantStatus("ten-mock", "disabled").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("PLATFORM_TENANT_PROTECTED");
    const detail = await fetchAdminTenantDetail("ten-mock");
    expect(detail.tenant.status).toBe("active");
  });

  it("音色槽位分配幂等：beta 挂 S_beta_777 两次 → 都 assigned:true，槽位表恰 1 条新记录；审计恰 1 条", async () => {
    const first = await assignVoiceSlot({ tenant_id: "ten-beta", speaker_id: "S_beta_777" });
    const second = await assignVoiceSlot({ tenant_id: "ten-beta", speaker_id: "S_beta_777" });
    expect(first).toEqual({ assigned: true });
    expect(second).toEqual({ assigned: true });
    const slots = await fetchAdminVoiceSlots();
    expect(slots.tenant_slots.filter((s) => s.speaker_id === "S_beta_777")).toHaveLength(1);
    const audit = await fetchAdminAudit({ action: "voice_slot_assign", limit: 20, offset: 0 });
    expect(audit.total).toBe(2); // seed 1 条 + 本次恰 1 条（幂等不重复落审计）
  });

  it("speaker_id 非法（不带 S_ 前缀）→ 422", async () => {
    const err = await assignVoiceSlot({ tenant_id: "ten-beta", speaker_id: "bad_id" }).catch((e) => e);
    expect((err as ApiError).status).toBe(422);
  });

  it("重跑回执三态（FIX1 冻结契约，确定值）：按量预计 / 固定价实扣 / 不重复扣费；审计各落一条；重复重跑 → 422", async () => {
    // ① task-f1：视频任务，失败已释放、按时长计费 → charged + credits=原预留 + is_estimate:true + 结算口径。
    const est = await retryAdminTask("task-f1");
    expect(est).toEqual({
      task_id: "task-f1",
      status: "queued",
      charged: true,
      credits: 1501,
      is_estimate: true,
      estimate_basis: "按实际成片时长结算"
    });
    // ② task-f3：视频反推（失败已释放）→ 固定价 100 实扣（is_estimate:false）。
    const fixed = await retryAdminTask("task-f3");
    expect(fixed).toEqual({ task_id: "task-f3", status: "queued", charged: true, credits: 100, is_estimate: false });
    // ③ task-f2：电商详情图复刻（确认时已扣）→ 不重复扣费。
    const free = await retryAdminTask("task-f2");
    expect(free).toEqual({ task_id: "task-f2", status: "queued", charged: false, credits: 0, is_estimate: false });
    // 审计：三次重跑各落一条（前→后）。
    const audit = await fetchAdminAudit({ action: "task_retry", limit: 20, offset: 0 });
    expect(audit.total).toBe(3);
    expect(audit.items[0]).toMatchObject({ before: { status: "failed" }, after: { status: "queued" }, target_tenant_slug: "gamma" });
    // 已回 queued → 非 failed 不可重跑（恰跑一次的服务端兜底）。
    const err = await retryAdminTask("task-f1").catch((e) => e);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("TASK_NOT_RETRYABLE");
  });
});
