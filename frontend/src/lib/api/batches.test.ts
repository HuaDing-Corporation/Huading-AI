import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { cancelBatch, createBatch, estimateBatch, getBatch, listBatches, retryBatchTask } from "./batches";
import type { BatchRequest } from "./types";

const ecomReq = (n: number, resolution = "720p"): BatchRequest => ({
  kind: "ecom_table",
  rows: Array.from({ length: n }, (_, i) => ({ product_name: `p${i}`, selling_points: "s", image_url: "http://x/1.png" })),
  common: { video_mode: "seedance_i2v", duration_sec: 30, resolution, apply_visible_label: false }
});

async function status(fn: () => Promise<unknown>): Promise<number | "ok"> {
  try {
    await fn();
    return "ok";
  } catch (e) {
    return (e as ApiError)?.status ?? -1;
  }
}

describe("batches API ↔ MSW（mock 忠实，据 seam 契约）", () => {
  it("estimate：小批充足 / 1080p 大批余额不足(insufficient=true)", async () => {
    const ok = await estimateBatch(ecomReq(5, "720p")); // 5×2=10 ≤ 50
    expect(ok.total_rows).toBe(5);
    expect(ok.per_row_credits).toBe(2);
    expect(ok.total_credits).toBe(10);
    expect(ok.insufficient).toBe(false);

    const big = await estimateBatch(ecomReq(30, "1080p")); // 30×5=150 > 50
    expect(big.total_credits).toBe(150);
    expect(big.insufficient).toBe(true);
  });

  it("estimate/create 422：空 / >30 / 缺必填", async () => {
    expect(await status(() => estimateBatch({ kind: "ecom_table", rows: [], common: {} }))).toBe(422);
    expect(await status(() => estimateBatch(ecomReq(31)))).toBe(422);
    expect(
      await status(() =>
        createBatch({ kind: "ecom_table", rows: [{ product_name: "", selling_points: "s", image_url: "u" }], common: {} })
      )
    ).toBe(422);
    // prompt_set 空 prompt
    expect(await status(() => createBatch({ kind: "prompt_set", rows: [{ prompt: "" }], common: {} }))).toBe(422);
  });

  it("create：充足→{batch_id,task_ids}；余额不足→422 INSUFFICIENT_CREDITS", async () => {
    const res = await createBatch(ecomReq(3, "720p"));
    expect(res.batch_id).toBeTruthy();
    expect(res.task_ids).toHaveLength(3);

    let caught: unknown;
    try {
      await createBatch(ecomReq(30, "1080p")); // 150 > 50
    } catch (e) {
      caught = e;
    }
    expect((caught as ApiError).status).toBe(422);
    expect((caught as ApiError).code).toBe("INSUFFICIENT_CREDITS");
  });

  it("detail 轮询渐进：多次 GET → 子任务推进到 done，末条 failed(partial_failed)", async () => {
    const { batch_id } = await createBatch(ecomReq(3, "720p"));
    let detail = await getBatch(batch_id);
    expect(detail.tasks).toHaveLength(3);
    // 多轮询推进到全终态
    for (let i = 0; i < 8 && detail.batch.status === "running"; i++) detail = await getBatch(batch_id);
    expect(detail.batch.status).toBe("partial_failed"); // 末条 failed，其余 done
    expect(detail.tasks.filter((t) => t.status === "done").length).toBe(2);
    const failed = detail.tasks.find((t) => t.status === "failed");
    expect(failed?.error).toBeTruthy();
  });

  it("list 含已建批次", async () => {
    const { batch_id } = await createBatch(ecomReq(2));
    const items = await listBatches();
    expect(items.some((b) => b.id === batch_id)).toBe(true);
  });

  it("cancel：未开跑 queued → cancelled", async () => {
    const { batch_id } = await createBatch(ecomReq(3));
    const after = await cancelBatch(batch_id); // 刚建全 queued → 全 cancelled
    expect(after.tasks.every((t) => t.status === "cancelled")).toBe(true);
    expect(after.batch.status).toBe("cancelled");
  });

  it("retry：failed 子任务 → running", async () => {
    const { batch_id } = await createBatch(ecomReq(2, "720p"));
    let detail = await getBatch(batch_id);
    for (let i = 0; i < 8 && detail.batch.status === "running"; i++) detail = await getBatch(batch_id);
    const failed = detail.tasks.find((t) => t.status === "failed");
    expect(failed).toBeTruthy();
    const r = await retryBatchTask(batch_id, failed!.task_id);
    expect(r.status).toBe("running");
  });

  it("retry → 下轮轮询恢复 done → completed（重试可恢复，mock 忠实）", async () => {
    const { batch_id } = await createBatch(ecomReq(2, "720p"));
    let detail = await getBatch(batch_id);
    for (let i = 0; i < 8 && detail.batch.status === "running"; i++) detail = await getBatch(batch_id);
    const failed = detail.tasks.find((t) => t.status === "failed")!;
    await retryBatchTask(batch_id, failed.task_id);
    let after = await getBatch(batch_id);
    for (let i = 0; i < 6 && after.batch.status !== "completed"; i++) after = await getBatch(batch_id);
    expect(after.batch.status).toBe("completed");
    expect(after.tasks.every((t) => t.status === "done")).toBe(true);
  });

  it("404：未知批次", async () => {
    expect(await status(() => getBatch("nope"))).toBe(404);
  });
});
