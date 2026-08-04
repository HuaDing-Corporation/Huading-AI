import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import {
  confirmEcomReplicate,
  ecomReplicateActualDimensions,
  getEcomReplicateJob,
  isEcomReplicateSettled,
  planEcomReplicate,
  retryEcomReplicateOutput,
  type EcomReplicatePlanInput
} from "./ecom-replicate";

// ECOM-REPLICATE-UI-0001 · FIX1：adapter ↔ MSW 镜像真实 BE PR #140 契约（路由 /replicate、product_info dict、
// plan.outputs 真字段、confirm minimal、GET 轮询、retry 单张按 index）。mock 先行，BE 合并后仅对齐本文件。

const baseInput = (over?: Partial<EcomReplicatePlanInput>): EcomReplicatePlanInput => ({
  output_mode: "main",
  reference_image_asset_ids: ["ref-1"],
  product_image_asset_ids: ["prod-1"],
  product_info: { description: "316 不锈钢保温杯" },
  selling_points: ["24 小时锁温"],
  ...over
});

describe("planEcomReplicate · POST /replicate（201，规划表，不扣费）", () => {
  it("主图：5 张 planned + total_credits 650(=5×130) + 1024x1024/1:1 + credit_rate 130", async () => {
    const job = await planEcomReplicate(baseInput({ output_mode: "main" }));
    expect(job.job_id).toBeTruthy();
    expect(job.status).toBe("plan_ready");
    expect(job.output_mode).toBe("main");
    expect(job.output_count).toBe(5);
    expect(job.total_credits).toBe(650);
    expect(job.credit_rate).toBe(130);
    expect(job.requested_size).toBe("1024x1024");
    expect(job.requested_aspect).toBe("1:1");
    expect(job.plan.outputs).toHaveLength(5);
    // 规划阶段每张 planned，未出图（asset/尺寸为空）
    expect(job.plan.outputs.every((o) => o.status === "planned")).toBe(true);
    expect(job.plan.outputs.every((o) => o.asset_id == null && o.actual_width == null)).toBe(true);
    // 真字段齐备（index/theme/requested_size/aspect）
    expect(job.plan.outputs[0]).toMatchObject({ index: 0, requested_size: "1024x1024", requested_aspect: "1:1" });
    expect(typeof job.plan.outputs[0].theme).toBe("string");
  });

  it("详情页：12 张 + total 1560 + 768x1024/3:4", async () => {
    const job = await planEcomReplicate(baseInput({ output_mode: "detail" }));
    expect(job.output_count).toBe(12);
    expect(job.total_credits).toBe(1560);
    expect(job.requested_size).toBe("768x1024");
    expect(job.requested_aspect).toBe("3:4");
    expect(job.plan.outputs).toHaveLength(12);
  });

  it("请求体 product_info 为 dict（非字符串）；plan 阶段 outputs 尚无 asset_id", async () => {
    const job = await planEcomReplicate(baseInput({ product_info: { name: "保温杯", material: "316 不锈钢" } }));
    expect(job.plan.outputs[0].asset_id).toBeNull();
  });

  it("缺模式/参考图/商品图/超上限(主图>5·详情>12·商品>4)/超 8 卖点/非 dict → 422", async () => {
    await expect(planEcomReplicate(baseInput({ output_mode: "x" as never }))).rejects.toMatchObject({ status: 422 });
    await expect(planEcomReplicate(baseInput({ reference_image_asset_ids: [] }))).rejects.toMatchObject({ status: 422 });
    await expect(planEcomReplicate(baseInput({ product_image_asset_ids: [] }))).rejects.toMatchObject({ status: 422 });
    // ECOM-REF-LIMIT：参考图上限随模式——主图 5 合法、6 超限 422；详情 12 合法、13 超限 422。
    await expect(
      planEcomReplicate(baseInput({ output_mode: "main", reference_image_asset_ids: Array.from({ length: 6 }, (_, i) => `r${i}`) }))
    ).rejects.toMatchObject({ status: 422 });
    await expect(
      planEcomReplicate(baseInput({ output_mode: "detail", reference_image_asset_ids: Array.from({ length: 13 }, (_, i) => `r${i}`) }))
    ).rejects.toMatchObject({ status: 422 });
    // 商品图恒 ≤4：5 张超限 422。
    await expect(
      planEcomReplicate(baseInput({ product_image_asset_ids: ["a", "b", "c", "d", "e"] }))
    ).rejects.toMatchObject({ status: 422 });
    await expect(
      planEcomReplicate(baseInput({ selling_points: Array.from({ length: 9 }, (_, i) => `p${i}`) }))
    ).rejects.toMatchObject({ status: 422 });
    await expect(
      planEcomReplicate(baseInput({ product_info: "字符串非 dict" as unknown as Record<string, unknown> }))
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("两阶段：confirm(minimal) → GET 轮询 → 结果", () => {
  it("confirm 只回 minimal（无 plan/outputs），status=generating；幂等再 confirm 不改", async () => {
    const job = await planEcomReplicate(baseInput());
    const confirmed = await confirmEcomReplicate(job.job_id);
    expect(confirmed.job_id).toBe(job.job_id);
    expect(confirmed.status).toBe("generating");
    expect(confirmed.output_count).toBe(5);
    expect(confirmed.total_credits).toBe(650);
    expect((confirmed as unknown as Record<string, unknown>).plan).toBeUndefined();
    // 幂等
    const again = await confirmEcomReplicate(job.job_id);
    expect(again.status).toBe("generating");
  });

  it("GET 轮询：第 2 次起逐张 succeeded（asset_id + actual_w/h + download_url），全部完成 → completed", async () => {
    const job = await planEcomReplicate(baseInput({ output_mode: "main" }));
    await confirmEcomReplicate(job.job_id);
    const poll1 = await getEcomReplicateJob(job.job_id);
    expect(poll1.status).toBe("generating");
    const poll2 = await getEcomReplicateJob(job.job_id);
    expect(poll2.status).toBe("completed");
    expect(poll2.plan.outputs.every((o) => o.status === "succeeded")).toBe(true);
    const o0 = poll2.plan.outputs[0];
    expect(o0.asset_id).toBeTruthy();
    expect(o0.download_url).toBeTruthy();
    // APIMart 非精确像素：1024x1024 → 实返 1254x1254
    expect(o0.actual_width).toBe(1254);
    expect(o0.actual_height).toBe(1254);
    expect(ecomReplicateActualDimensions(o0)).toBe("1254x1254");
  });

  it("__FAIL__ → partial_failed（首张 failed）→ retry(index 0) 回 planned → GET 完成 completed", async () => {
    const job = await planEcomReplicate(baseInput({ product_info: { description: "__FAIL__ 保温杯" } }));
    await confirmEcomReplicate(job.job_id);
    await getEcomReplicateJob(job.job_id);
    const settled = await getEcomReplicateJob(job.job_id);
    expect(settled.status).toBe("partial_failed");
    expect(settled.plan.outputs[0].status).toBe("failed");
    // 单张重试：返回该张最新态（planned），不重复扣费（无 confirm）
    const retried = await retryEcomReplicateOutput(job.job_id, 0);
    expect(retried.index).toBe(0);
    expect(retried.status).toBe("planned");
    // 重试后整单回 generating，轮询至完成
    await getEcomReplicateJob(job.job_id);
    const done = await getEcomReplicateJob(job.job_id);
    expect(done.status).toBe("completed");
    expect(done.plan.outputs[0].status).toBe("succeeded");
  });
});

describe("isEcomReplicateSettled / ecomReplicateActualDimensions", () => {
  it("settled 判定覆盖 completed/partial_failed/failed/cancelled；planning/plan_ready/generating 未终态", () => {
    expect(isEcomReplicateSettled("completed")).toBe(true);
    expect(isEcomReplicateSettled("partial_failed")).toBe(true);
    expect(isEcomReplicateSettled("failed")).toBe(true);
    expect(isEcomReplicateSettled("cancelled")).toBe(true);
    expect(isEcomReplicateSettled("generating")).toBe(false);
    expect(isEcomReplicateSettled("plan_ready")).toBe(false);
    expect(isEcomReplicateSettled("planning")).toBe(false);
  });

  it("尺寸：actual_w/h 均在才给「WxH」；缺一即 null（不冒充 requested_size）", () => {
    const base = {
      id: "o",
      index: 0,
      theme: "hero",
      requested_size: "768x1024",
      requested_aspect: "3:4",
      status: "succeeded" as const
    };
    expect(ecomReplicateActualDimensions({ ...base, actual_width: 1086, actual_height: 1448 })).toBe("1086x1448");
    expect(ecomReplicateActualDimensions({ ...base, actual_width: null, actual_height: 1448 })).toBeNull();
    expect(ecomReplicateActualDimensions({ ...base, actual_width: 1086, actual_height: null })).toBeNull();
  });
});
