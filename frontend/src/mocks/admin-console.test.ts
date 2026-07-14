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

// 管理员后台 mock 契约承重（ADMIN-CONSOLE-UI-0001 · FIX1 对齐真实 BE #165）：真打 MSW（不 mock adapter/hooks），
// 断言全确定值（toBe/toEqual）——禁止量级/范围断言承担安全职责。分页 page/page_size、路径 /audit-logs、
// PATCH plan/status、subscription 快照、槽位合一列表、重试回执三态（无 estimate_basis）逐字段对齐真契约。
// ⚠️ 本文件 mock 数据为模块级内存态：只读用例在前、写操作在后（顺序即契约）。

const LIST = { sort: "created_at" as const, order: "desc" as const, page: 1, page_size: 20 };

function asNonPlatform() {
  localStorage.setItem("hd_mock_platform", "0");
  localStorage.setItem("hd_mock_plan", "free");
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("门禁：非平台（新注册态）→ 全端点 403 PLATFORM_ADMIN_REQUIRED（与 /me admin_console 同源）", () => {
  it("tenants / voice-slots / usage / tasks / audit-logs / 写操作 全部 403，不泄任何数据", async () => {
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
    await expect403(fetchAdminUsage({ page: 1, page_size: 20 }));
    await expect403(fetchAdminTasks({ page: 1, page_size: 20 }));
    await expect403(fetchAdminAudit({ page: 1, page_size: 20 }));
    await expect403(adjustTenantCredits("ten-acme", { delta: 100, reason: "x" }));
    await expect403(retryAdminTask("task-f1"));
    await expect403(exportAdminUsageCsv({}));
  });
});

describe("平台账号（默认）· 读端点确定值（真契约字段）", () => {
  it("tenants：total=4 + page/page_size 回显；created_at desc 首位 beta；q=acme → 恰 1 条含订阅快照；plan=huading → 恰 2 条", async () => {
    const all = await fetchAdminTenants(LIST);
    expect(all.total).toBe(4);
    expect(all.page).toBe(1);
    expect(all.page_size).toBe(20);
    expect(all.items[0].slug).toBe("beta"); // 2026-07-01 最新
    expect(all.items[0].subscription).toBeNull(); // 无生效订阅（可空契约）
    const searched = await fetchAdminTenants({ ...LIST, q: "acme" });
    expect(searched.total).toBe(1);
    expect(searched.items[0].slug).toBe("acme");
    expect(searched.items[0].subscription).toEqual({ id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 });
    const huading = await fetchAdminTenants({ ...LIST, plan: "huading" });
    expect(huading.total).toBe(2); // huading（平台自己）+ acme
  });

  it("tenants 排序：sort=balance&order=desc → 首位 acme（remaining 14000）；sort=credits_used&order=desc → 首位 acme（used 5000）", async () => {
    const byBalance = await fetchAdminTenants({ ...LIST, sort: "balance", order: "desc" });
    expect(byBalance.items[0].slug).toBe("acme");
    const byUsed = await fetchAdminTenants({ ...LIST, sort: "credits_used", order: "desc" });
    expect(byUsed.items[0].slug).toBe("acme");
  });

  it("tenant 详情：acme 基础信息 + 最近任务/用量/专属槽位（真字段）确定值", async () => {
    const d = await fetchAdminTenantDetail("ten-acme");
    expect(d.tenant.slug).toBe("acme");
    expect(d.tenant.status).toBe("active");
    expect(d.recent_tasks.map((t) => t.id)).toEqual(["task-q1", "task-r1", "task-f1"]); // created_at desc（镜像 BE）
    expect(d.recent_tasks[0].task_family).toBe("video");
    expect(d.recent_tasks[2].retryable).toBe(true); // task-f1
    expect(d.recent_usage).toHaveLength(4); // u-1/u-2/u-4/u-6
    // 镜像 BE routes:87：按 tenant_id 过滤——含被该租户占用的平台池槽。
    expect(d.voice_slots.map((v) => v.speaker_id)).toEqual(["S_pool_001", "S_acme_001"]);
    expect(d.voice_slots[1]).toMatchObject({ speaker_id: "S_acme_001", scope: "tenant", brand_voice_name: "我的主播音" });
  });

  it("voice-slots：合一列表 total=4（平台 3 + 租户 1）、remaining=2（全列表未占用，镜像 BE）", async () => {
    const s = await fetchAdminVoiceSlots();
    expect(s.total).toBe(4);
    expect(s.remaining).toBe(2); // 全列表未占用（镜像 BE sum(not occupied)）
    expect(s.items.filter((i) => i.scope === "platform")).toHaveLength(3);
    expect(s.items[0]).toMatchObject({ speaker_id: "S_pool_001", scope: "platform", occupied: true, tenant_slug: "acme", brand_voice_name: "我的主播音" });
    expect(s.items.filter((i) => i.scope === "tenant")).toEqual([
      expect.objectContaining({ speaker_id: "S_acme_001", tenant_id: "ten-acme", occupied: true })
    ]);
  });

  it("usage：全量 6；tenant_id=ten-beta → 恰 1 条（u-3，真字段 tenant_slug/video_task_id）；status=released → 恰 1 条", async () => {
    const all = await fetchAdminUsage({ page: 1, page_size: 20 });
    expect(all.total).toBe(6);
    const beta = await fetchAdminUsage({ tenant_id: "ten-beta", page: 1, page_size: 20 });
    expect(beta.total).toBe(1);
    expect(beta.items[0]).toMatchObject({ id: "u-3", tenant_id: "ten-beta", tenant_slug: "beta", tenant_name: "贝塔传媒", credits: 10, cost_cents: 30, video_task_id: "task-d1" });
    const released = await fetchAdminUsage({ status: "released", page: 1, page_size: 20 });
    expect(released.total).toBe(1);
    expect(released.items[0].id).toBe("u-4");
  });

  it("tasks：全量 6；status=failed → 恰 3 条（done 别名归一 succeeded）；task_family=reverse_prompt → 恰 1 条；video → 4 条", async () => {
    const all = await fetchAdminTasks({ page: 1, page_size: 20 });
    expect(all.total).toBe(6);
    const failed = await fetchAdminTasks({ status: "failed", page: 1, page_size: 20 });
    expect(failed.total).toBe(3);
    // done 是 BE 兼容别名 → 归一 succeeded（task-d1）。
    const done = await fetchAdminTasks({ status: "done" as never, page: 1, page_size: 20 });
    expect(done.total).toBe(1);
    expect(done.items[0].id).toBe("task-d1");
    const reverse = await fetchAdminTasks({ task_family: "reverse_prompt", page: 1, page_size: 20 });
    expect(reverse.total).toBe(1);
    expect(reverse.items[0]).toMatchObject({ id: "task-f3", task_family: "reverse_prompt", status: "failed", retryable: true, error_code: "PROVIDER_ERROR" });
    const video = await fetchAdminTasks({ task_family: "video", page: 1, page_size: 20 });
    expect(video.total).toBe(4);
    // succeeded（真枚举，非 done）
    const d1 = video.items.find((t) => t.id === "task-d1");
    expect(d1?.status).toBe("succeeded");
    expect(d1?.retryable).toBe(false);
  });

  it("CSV 导出：无筛选 6 行 > mock 上限 3 → 422 USAGE_EXPORT_TOO_LARGE 中文原样；tenant_id=ten-beta → 200 CSV（BOM + 13 列真表头 + 1 行）", async () => {
    const err = await exportAdminUsageCsv({}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("USAGE_EXPORT_TOO_LARGE");
    expect((err as ApiError).message).toBe("导出记录超过 3 条，请缩小时间范围。");
    const blob = await exportAdminUsageCsv({ tenant_id: "ten-beta" });
    // BOM 在**字节层**断言（readAsText 按标准会剥 UTF-8 BOM，文本层看不到）：前 3 字节 = EF BB BF。
    const bytes = await new Promise<Uint8Array>((resolve) => {
      const fr = new FileReader();
      fr.onload = () => resolve(new Uint8Array(fr.result as ArrayBuffer));
      fr.readAsArrayBuffer(blob);
    });
    expect([bytes[0], bytes[1], bytes[2]]).toEqual([0xef, 0xbb, 0xbf]); // BOM（镜像 BE ﻿ 前缀）
    // 文本层（BOM 已被 readAsText 剥掉）：13 列真表头 + 1 数据行。
    const text = await new Promise<string>((resolve) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.readAsText(blob);
    });
    const lines = text.split("\r\n");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe("created_at,tenant_id,tenant_slug,tenant_name,capability,provider,model,quantity,unit,credits,cost_cents,status,video_task_id");
    expect(lines[1]).toBe("2026-07-11T09:02:00Z,ten-beta,beta,贝塔传媒,copywriting,deepseek,v3,1,篇,10,30,settled,task-d1");
  });
});

describe("平台账号 · 写操作（顺序即契约：写会改内存态）", () => {
  it("余额 +5000（acme）：响应 {tenant_id, delta, subscription} 确定值；审计 before/after = 订阅快照全量 dump + 理由", async () => {
    const res = await adjustTenantCredits("ten-acme", { delta: 5000, reason: "线下打款充值" });
    expect(res).toEqual({
      tenant_id: "ten-acme",
      delta: 5000,
      subscription: { id: "sub-acme", total: 25000, used: 5000, reserved: 1000, remaining: 19000 }
    });
    const audit = await fetchAdminAudit({ action: "credits_adjust", page: 1, page_size: 20 });
    expect(audit.total).toBe(1);
    expect(audit.items[0]).toMatchObject({
      action: "credits_adjust",
      target_tenant_id: "ten-acme",
      target_tenant_slug: "acme",
      target_id: "sub-acme",
      before: { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 },
      after: { id: "sub-acme", total: 25000, used: 5000, reserved: 1000, remaining: 19000 },
      reason: "线下打款充值"
    });
  });

  it("下限保护：gamma 扣 -300 → 422 CREDIT_TOTAL_BELOW_COMMITTED，message 逐字 =「扣减后额度会低于已用+预留，无法执行。」，余额不变", async () => {
    const err = await adjustTenantCredits("ten-gamma", { delta: -300, reason: "回收" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("CREDIT_TOTAL_BELOW_COMMITTED");
    expect((err as ApiError).message).toBe("扣减后额度会低于已用+预留，无法执行。");
    const detail = await fetchAdminTenantDetail("ten-gamma");
    expect(detail.tenant.subscription?.total).toBe(5000); // 拒绝后分文未动
  });

  it("无生效订阅（beta）调余额 → 404 ACTIVE_SUBSCRIPTION_NOT_FOUND（镜像 BE）", async () => {
    const err = await adjustTenantCredits("ten-beta", { delta: 100, reason: "x" }).catch((e) => e);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).code).toBe("ACTIVE_SUBSCRIPTION_NOT_FOUND");
  });

  it("停用平台租户自己（ten-mock，PATCH active:false）→ 422 CANNOT_SUSPEND_PLATFORM_TENANT，状态不变", async () => {
    const err = await changeTenantStatus("ten-mock", false).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe("CANNOT_SUSPEND_PLATFORM_TENANT");
    const detail = await fetchAdminTenantDetail("ten-mock");
    expect(detail.tenant.status).toBe("active");
  });

  it("启用 gamma（PATCH active:true）→ {tenant_id, status:\"active\"}；审计 status_change 前→后", async () => {
    const res = await changeTenantStatus("ten-gamma", true);
    expect(res).toEqual({ tenant_id: "ten-gamma", status: "active" });
    const audit = await fetchAdminAudit({ action: "status_change", page: 1, page_size: 20 });
    expect(audit.total).toBe(1);
    expect(audit.items[0]).toMatchObject({ before: { status: "suspended" }, after: { status: "active" } });
  });

  it("音色槽位分配幂等（POST /tenants/{id}/voice-slots）：首次 changed:true，重复 changed:false；审计**无条件**各落一条（镜像 BE）", async () => {
    const first = await assignVoiceSlot("ten-beta", { speaker_id: "S_beta_777" });
    expect(first).toEqual({ tenant_id: "ten-beta", speaker_id: "S_beta_777", changed: true, speaker_ids: ["S_beta_777"] });
    const second = await assignVoiceSlot("ten-beta", { speaker_id: "S_beta_777" });
    expect(second).toEqual({ tenant_id: "ten-beta", speaker_id: "S_beta_777", changed: false, speaker_ids: ["S_beta_777"] });
    const slots = await fetchAdminVoiceSlots();
    expect(slots.items.filter((s) => s.speaker_id === "S_beta_777")).toHaveLength(1);
    const audit = await fetchAdminAudit({ action: "voice_slot_assign", page: 1, page_size: 20 });
    expect(audit.total).toBe(3); // seed 1 + 两次分配各 1（BE record_audit 无条件写，幂等重复也记 changed:false）
    expect(audit.items[0]).toMatchObject({ before: { speaker_ids: ["S_beta_777"] }, after: { speaker_id: "S_beta_777", changed: false, speaker_ids: ["S_beta_777"] } });
    expect(audit.items[1]).toMatchObject({ before: { speaker_ids: [] }, after: { speaker_id: "S_beta_777", changed: true, speaker_ids: ["S_beta_777"] } });
  });

  it("speaker_id 非法（不带 S_ 前缀）→ 422", async () => {
    const err = await assignVoiceSlot("ten-beta", { speaker_id: "bad_id" }).catch((e) => e);
    expect((err as ApiError).status).toBe(422);
  });

  it("重跑回执三态（202，真契约字段 id/task_family/tenant_id，无 estimate_basis）；审计各 1 条；重复重跑 → 409", async () => {
    // ① task-f1：released avatar_talk → charged + credits=原预留 + is_estimate:true（唯一 estimate）。
    const est = await retryAdminTask("task-f1", "video");
    expect(est).toEqual({ id: "task-f1", task_family: "video", tenant_id: "ten-acme", status: "queued", progress: 0, charged: true, credits: 1501, is_estimate: true });
    // ② task-f3：released 反推固定价 100 → is_estimate:false。
    const fixed = await retryAdminTask("task-f3", "reverse_prompt");
    expect(fixed).toEqual({ id: "task-f3", task_family: "reverse_prompt", tenant_id: "ten-beta", status: "queued", progress: 0, charged: true, credits: 100, is_estimate: false });
    // ③ task-f2：电商复刻（确认时已扣）→ 不重复扣费。
    const free = await retryAdminTask("task-f2");
    expect(free).toEqual({ id: "task-f2", task_family: "ecom_replicate", tenant_id: "ten-gamma", status: "queued", progress: 0, charged: false, credits: 0, is_estimate: false });
    // 审计三条（前→后）。
    const audit = await fetchAdminAudit({ action: "task_retry", page: 1, page_size: 20 });
    expect(audit.total).toBe(3);
    expect(audit.items[0]).toMatchObject({ before: { status: "failed", progress: 0 }, after: { status: "queued", progress: 0 }, target_tenant_slug: "gamma" });
    // 已回 queued（retryable:false）→ 422（恰跑一次的服务端兜底）。
    const err = await retryAdminTask("task-f1").catch((e) => e);
    expect((err as ApiError).status).toBe(409); // 镜像 BE（409 非 422）
    expect((err as ApiError).code).toBe("TASK_NOT_RETRYABLE");
  });
});
