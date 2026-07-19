import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { cancelBatch, createBatch, estimateBatch, getBatch, listBatches } from "./batches";
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

  it("行校验 422 BATCH_ROW_INVALID：空 / >30 / 缺必填 / 图非二选一(都给或都不给) / 空 prompt", async () => {
    const codeOf = async (fn: () => Promise<unknown>) => {
      try {
        await fn();
        return "ok";
      } catch (e) {
        return (e as ApiError)?.code;
      }
    };
    expect(await status(() => estimateBatch({ kind: "ecom_table", rows: [], common: { video_mode: "seedance_i2v" } }))).toBe(422);
    expect(await status(() => estimateBatch(ecomReq(31)))).toBe(422);
    // 都不给图
    expect(await codeOf(() => createBatch({ kind: "ecom_table", rows: [{ product_name: "a", selling_points: "s" }], common: { video_mode: "seedance_i2v" } }))).toBe("BATCH_ROW_INVALID");
    // 都给图（image_asset_id + image_url）→ XOR 违反
    expect(await codeOf(() => createBatch({ kind: "ecom_table", rows: [{ product_name: "a", selling_points: "s", image_asset_id: "id", image_url: "u" } as never], common: { video_mode: "seedance_i2v" } }))).toBe("BATCH_ROW_INVALID");
    expect(await codeOf(() => createBatch({ kind: "prompt_set", rows: [{ prompt: "" }], common: { video_mode: "video_gen" } }))).toBe("BATCH_ROW_INVALID");
  });

  it("common extra=forbid：多传字段 / 缺 video_mode / kind↔video_mode 不匹配 → 422 VALIDATION_ERROR（对齐后端 FastAPI）", async () => {
    const codeOf = async (fn: () => Promise<unknown>) => {
      try {
        await fn();
        return "ok";
      } catch (e) {
        return (e as ApiError)?.code;
      }
    };
    const rows = [{ prompt: "a" }];
    expect(await codeOf(() => createBatch({ kind: "prompt_set", rows, common: { video_mode: "video_gen", foo: 1 } as never }))).toBe("VALIDATION_ERROR");
    expect(await codeOf(() => createBatch({ kind: "prompt_set", rows, common: {} as never }))).toBe("VALIDATION_ERROR");
    expect(await codeOf(() => createBatch({ kind: "prompt_set", rows, common: { video_mode: "seedance_i2v" } }))).toBe("VALIDATION_ERROR");
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

  it("detail 轮询渐进：多次 GET → done，末条 failed(partial_failed) + error_code/error_message", async () => {
    const { batch_id } = await createBatch(ecomReq(3, "720p"));
    let detail = await getBatch(batch_id);
    expect(detail.tasks).toHaveLength(3);
    for (let i = 0; i < 8 && detail.batch.status === "running"; i++) detail = await getBatch(batch_id);
    expect(detail.batch.status).toBe("partial_failed");
    expect(detail.batch.common_params).toBeTruthy(); // BatchSummary 含 common_params
    expect(detail.tasks.filter((t) => t.status === "done").length).toBe(2);
    const failed = detail.tasks.find((t) => t.status === "failed");
    expect(failed?.error_code).toBe("BATCH_IMAGE_DOWNLOAD_FAILED");
    expect(failed?.error_message).toBeTruthy();
  });

  it("list 含已建批次", async () => {
    const { batch_id } = await createBatch(ecomReq(2));
    const items = await listBatches();
    expect(items.some((b) => b.id === batch_id)).toBe(true);
  });

  it("cancel：返回 {batch_id, cancelled, running}（逐字对齐后端）；queued → cancelled", async () => {
    const { batch_id } = await createBatch(ecomReq(3));
    const res = await cancelBatch(batch_id); // 刚建全 queued → 全 cancelled
    expect(res).toEqual({ batch_id, cancelled: 3, running: 0 });
    const after = await getBatch(batch_id);
    expect(after.tasks.every((t) => t.status === "cancelled")).toBe(true);
    expect(after.batch.status).toBe("cancelled");
  });

  it("404：未知批次", async () => {
    expect(await status(() => getBatch("nope"))).toBe(404);
  });

  // ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX2（CB P1 · 机制）：批量 common.duration_sec 也是 int——小数 → 422
  // VALIDATION_ERROR（前端 ecom-table-form durationOk=isValidDuration 已从源头拦；此为 mock 拒非整数，未加门即 red）。
  it("机制：common.duration_sec 小数(5.5) → 422 VALIDATION_ERROR（estimate + create 皆拒）", async () => {
    const frac: BatchRequest = {
      kind: "ecom_table",
      rows: [{ product_name: "p", selling_points: "s", image_url: "http://x/1.png" }],
      common: { video_mode: "seedance_i2v", duration_sec: 5.5, resolution: "720p", apply_visible_label: false }
    };
    expect(await status(() => estimateBatch(frac))).toBe(422);
    expect(await status(() => createBatch(frac))).toBe(422);
  });
});
