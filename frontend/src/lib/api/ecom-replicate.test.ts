import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import {
  confirmEcomReplicate,
  getEcomReplicateJob,
  isEcomReplicateSettled,
  planEcomReplicate,
  retryEcomReplicateOutput,
  type EcomReplicatePlanInput
} from "./ecom-replicate";

// ECOM-REPLICATE-UI-0001：真 apiFetch → MSW（mock 忠实两阶段状态机）。验证 plan/confirm/get/retry 契约 +
// **total_credits 取后端**（不前端硬编码）+ 扣费幂等 + 单张重试不重复扣 + 禁词 + 参考图不足循环复用。

const base = (over: Partial<EcomReplicatePlanInput> = {}): EcomReplicatePlanInput => ({
  output_mode: "main",
  reference_image_asset_ids: ["a1", "a2", "a3", "a4"],
  product_image_asset_ids: ["p1"],
  product_info: "316 不锈钢保温杯",
  selling_points: ["24 小时锁温", "便携轻巧"],
  ...over
});

async function planToCompleted(input: EcomReplicatePlanInput) {
  const planned = await planEcomReplicate(input);
  await confirmEcomReplicate(planned.job_id);
  let job = await getEcomReplicateJob(planned.job_id);
  for (let i = 0; i < 5 && !isEcomReplicateSettled(job); i++) job = await getEcomReplicateJob(planned.job_id);
  return { jobId: planned.job_id, job };
}

describe("ecom-replicate plan（阶段一·规划表 + total_credits）", () => {
  it("主图：plan_ready、5 张（第 5 张白底）、total_credits=75、尺寸 1024x1024、不扣费(outputs 空)", async () => {
    const job = await planEcomReplicate(base({ output_mode: "main" }));
    expect(job.status).toBe("plan_ready");
    expect(job.output_mode).toBe("main");
    expect(job.plan).toHaveLength(5);
    expect(job.total_credits).toBe(75); // 5×15，后端取
    expect(job.plan[4].ref_label).toBe("白底图");
    expect(job.plan.every((p) => p.requested_size === "1024x1024")).toBe(true);
    expect(job.plan.every((p) => p.no_crop_notice === "原图输出，不裁剪")).toBe(true);
    expect(job.outputs).toEqual([]); // 规划阶段不生成、不扣费
  });

  it("详情页：12 张、total_credits=180、尺寸 768x1024", async () => {
    const job = await planEcomReplicate(base({ output_mode: "detail", reference_image_asset_ids: ["a1"] }));
    expect(job.plan).toHaveLength(12);
    expect(job.total_credits).toBe(180);
    expect(job.plan.every((p) => p.requested_size === "768x1024")).toBe(true);
  });

  it("参考图不足（detail 只给 1 张）→ 循环复用标注 reused=true", async () => {
    const job = await planEcomReplicate(base({ output_mode: "detail", reference_image_asset_ids: ["a1"] }));
    expect(job.plan[0].reused).toBe(false); // 第 1 页用 ref_001
    expect(job.plan.some((p) => p.reused)).toBe(true); // 后续循环复用
  });

  it("缺模式 / 缺参考图 / 缺商品图 → 422（friendly，不 500）", async () => {
    await expect(planEcomReplicate(base({ output_mode: "" as never }))).rejects.toBeInstanceOf(ApiError);
    await expect(planEcomReplicate(base({ reference_image_asset_ids: [] }))).rejects.toBeInstanceOf(ApiError);
    await expect(planEcomReplicate(base({ product_image_asset_ids: [] }))).rejects.toBeInstanceOf(ApiError);
  });

  it("承重·禁词（§13）：卖点含「全网最低」→ 422 ECOM_BANNED_WORD", async () => {
    let caught: unknown;
    try {
      await planEcomReplicate(base({ selling_points: ["全网最低价"] }));
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).code).toBe("ECOM_BANNED_WORD");
  });
});

describe("ecom-replicate confirm/get/retry（扣费门 + 轮询 + 单张重试）", () => {
  it("confirm → generating；轮询后 completed，每张原图 + requested vs actual 尺寸", async () => {
    const { job } = await planToCompleted(base());
    expect(job.status).toBe("completed");
    expect(job.outputs).toHaveLength(5);
    expect(job.outputs.every((o) => o.status === "succeeded")).toBe(true);
    expect(job.outputs.every((o) => !!o.download_url)).toBe(true); // 原图下载 URL
    // requested 1024x1024 → actual 非精确像素（APIMart）1254x1254，契约同时记录
    expect(job.outputs[0].requested_size).toBe("1024x1024");
    expect(job.outputs[0].actual_dimensions).toBe("1254x1254");
  });

  it("承重·扣费幂等：completed 后再次 confirm 不重置为 generating（不重复扣）", async () => {
    const { jobId, job } = await planToCompleted(base());
    expect(job.status).toBe("completed");
    const again = await confirmEcomReplicate(jobId);
    expect(again.status).toBe("completed"); // 幂等：不回退 generating、不重扣
    expect(again.outputs.every((o) => o.status === "succeeded")).toBe(true);
  });

  it("承重·单张失败 → partial_failed；单张 retry → completed（不重复扣、清失败）", async () => {
    const { jobId, job } = await planToCompleted(base({ product_info: "保温杯 __FAIL__" }));
    expect(job.status).toBe("partial_failed");
    const failed = job.outputs.find((o) => o.status === "failed")!;
    expect(failed.page_no).toBe(1);
    const retried = await retryEcomReplicateOutput(jobId, failed.page_no);
    expect(retried.status).toBe("completed");
    expect(retried.outputs.find((o) => o.page_no === 1)!.status).toBe("succeeded");
  });
});
