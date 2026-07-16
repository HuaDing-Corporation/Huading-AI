import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  deleteReversePromptJob,
  getReversePromptJob,
  listReversePromptJobs,
  regenerateReversePrompt,
  saveReversePrompt,
  type ReverseSourceKind
} from "@/lib/api/reverse-prompt";

// HISTORY-VIDEO-REVERSE-UI-0001 · 反推历史契约承重（真打 MSW /reverse-prompt/jobs，不 mock adapter）。
// 逐字段对齐**已合入 develop 的真 BE**（非冻结文档摘要）：
//   backend/app/schemas/reverse_prompt.py:92-110（列表项 6 字段 / 列表响应 / 软删响应）
//   backend/app/api/v1/routes/reverse_prompt.py:64-87（source_kind Literal + page/page_size 上下界）、:152-166（软删）
//   backend/app/services/reverse_prompt.py:276（列表过滤软删）、:320-322（video 无缩略图）、:332-339（summary 截断）
// 全确定值断言；期望值手写、不 import 被测代码的常量。
beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("反推历史列表 GET /reverse-prompt/jobs", () => {
  it("省略 source_kind = 全部：6 条（image 3 + video 3），按 created_at 倒序；分页默认 page=1/page_size=20", async () => {
    const r = await listReversePromptJobs();
    expect(r.total).toBe(6);
    expect(r.page).toBe(1);
    expect(r.page_size).toBe(20); // BE routes:75 默认 20
    expect(r.items.map((i) => i.id)).toEqual(["rh-img-1", "rh-img-2", "rh-img-3", "rh-vid-1", "rh-vid-2", "rh-vid-3"]);
  });

  it("source_kind=image：恰 3 条，且都有 source_thumbnail_url", async () => {
    const r = await listReversePromptJobs({ source_kind: "image" });
    expect(r.total).toBe(3);
    expect(r.items.every((i) => i.source_kind === "image")).toBe(true);
    expect(r.items.every((i) => typeof i.source_thumbnail_url === "string" && i.source_thumbnail_url.length > 0)).toBe(
      true
    );
  });

  // 🔴 BE 事实（services/reverse_prompt.py:320-322）：缩略图只对 image 源生成，video 源恒 null。
  // UI 必须为「视频反推无缩略图」设计占位，不能假设有图。
  it("source_kind=video：恰 3 条，且 source_thumbnail_url 恒 null（BE 只给图片源生成缩略图）", async () => {
    const r = await listReversePromptJobs({ source_kind: "video" });
    expect(r.total).toBe(3);
    expect(r.items.every((i) => i.source_kind === "video")).toBe(true);
    expect(r.items.every((i) => i.source_thumbnail_url === null)).toBe(true);
  });

  // 🔴 列表项无 result → 「带入生成」拿不到 fill_targets，必须先取详情（这决定了交互形态）。
  it("列表项恰 6 个字段、不含 result（BE ReversePromptHistoryItem schemas:97-103）", async () => {
    const r = await listReversePromptJobs({ source_kind: "image" });
    expect(Object.keys(r.items[0]).sort()).toEqual([
      "created_at",
      "id",
      "source_kind",
      "source_thumbnail_url",
      "status",
      "summary"
    ]);
  });

  it("状态如实透出 5 值（DB CheckConstraint models.py:471）：succeeded/saved/failed/running/queued 都能看到", async () => {
    const r = await listReversePromptJobs();
    expect(r.items.map((i) => i.status)).toEqual(["succeeded", "saved", "failed", "succeeded", "running", "queued"]);
  });

  it("未完成/失败的记录 summary 为 null（BE 无 result 即无 summary）", async () => {
    const r = await listReversePromptJobs();
    const byId = Object.fromEntries(r.items.map((i) => [i.id, i]));
    expect(byId["rh-img-3"].summary).toBeNull(); // failed
    expect(byId["rh-vid-2"].summary).toBeNull(); // running
    expect(byId["rh-vid-3"].summary).toBeNull(); // queued
    expect(byId["rh-img-1"].summary).toContain("保温杯"); // succeeded 有摘要
  });

  it("🔴 非法 source_kind → 422（mock 不比 BE 宽松：BE 是 Literal 校验，不是 200 空列表）", async () => {
    await expect(listReversePromptJobs({ source_kind: "bogus" as ReverseSourceKind })).rejects.toMatchObject({
      status: 422,
      code: "VALIDATION_ERROR"
    });
  });

  // 🔴 FIX2 · P1-2：BE 是 `page: Query(ge=1)` / `page_size: Query(ge=1, le=100)`（routes:74-75）→ 越界即 422。
  // 上一版 mock 把越界值 clamp 成合法值返 200 = 比 BE 宽松（本项目第三次犯：#159 / #166 都栽过）。
  it("🔴 分页越界 → 422（page=0 / page_size=0 / page_size=101；mock 不比 BE 宽松）", async () => {
    await expect(listReversePromptJobs({ page: 0 })).rejects.toMatchObject({ status: 422, code: "VALIDATION_ERROR" });
    await expect(listReversePromptJobs({ page: -1 })).rejects.toMatchObject({ status: 422 });
    await expect(listReversePromptJobs({ page_size: 0 })).rejects.toMatchObject({ status: 422 });
    await expect(listReversePromptJobs({ page_size: 101 })).rejects.toMatchObject({ status: 422 });
  });

  it("分页边界内合法：page_size=100（BE le=100 的上界，属合法）", async () => {
    const r = await listReversePromptJobs({ page_size: 100 });
    expect(r.page_size).toBe(100);
    expect(r.total).toBe(6);
  });
});

describe("反推详情 GET /reverse-prompt/jobs/{id}", () => {
  it("succeeded(image)：含 result + fill_targets 6 键；video_analysis 为 null（图片源）", async () => {
    const job = await getReversePromptJob("rh-img-1");
    expect(job.status).toBe("succeeded");
    expect(job.source_kind).toBe("image");
    expect(job.result?.prompt_zh).toContain("保温杯");
    expect(job.result?.prompt_en).toContain("insulated bottle");
    expect(Object.keys(job.result!.fill_targets).sort()).toEqual([
      "avatar_talk",
      "ecom_model",
      "ecom_poster",
      "photo",
      "seedance_i2v",
      "video_gen"
    ]);
    expect(job.result?.video_analysis ?? null).toBeNull();
  });

  it("succeeded(video)：result.video_analysis 内嵌（duration_sec>0 + pacing 枚举 + shot_list）", async () => {
    const job = await getReversePromptJob("rh-vid-1");
    expect(job.source_kind).toBe("video");
    expect(job.result?.video_analysis?.duration_sec).toBe(18);
    expect(job.result?.video_analysis?.pacing).toBe("fast"); // BE Literal["slow","medium","fast","variable"]
    expect(job.result?.video_analysis?.shot_list).toHaveLength(4);
    expect(job.result?.video_analysis?.shot_list[0]).toMatchObject({ index: 0, start_sec: 0, end_sec: 4 });
  });

  it("failed：无 result，但有 error_code / error_message（供 UI 展示失败原因）", async () => {
    const job = await getReversePromptJob("rh-img-3");
    expect(job.status).toBe("failed");
    expect(job.result ?? null).toBeNull();
    expect(job.error_code).toBe("REVERSE_FAILED");
    expect(job.error_message).toBe("图片解析失败，请换一张更清晰的图片重试");
  });

  it("queued / running：无 result（未完成）", async () => {
    expect((await getReversePromptJob("rh-vid-3")).result ?? null).toBeNull();
    expect((await getReversePromptJob("rh-vid-2")).result ?? null).toBeNull();
  });

  it("saved：saved_at 非空（BE 状态机 succeeded→saved）", async () => {
    const job = await getReversePromptJob("rh-img-2");
    expect(job.status).toBe("saved");
    expect(job.saved_at).toBeTruthy();
    expect(job.result).toBeTruthy(); // saved 仍带 result
  });

  // 🔴 FIX2 · §六.5 自扫出的**第四处宽松**（Codex B 没提，我自己扫的）：旧 mock 详情有个兜底
  // `return ok(reverseJobRead(id))` —— **任何不存在的 id 都返 200 + 一份假 job**；BE 对不存在/跨租户一律
  // 经 reverse_prompt_job_or_404 → 404。这是「mock 比 BE 宽松」的同一种病，只是没人写测试碰过它。
  it("不存在的 id → 404（旧 mock 兜底恒返假 job = 第四处宽松）", async () => {
    await expect(getReversePromptJob("rh-not-exist")).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_JOB_NOT_FOUND"
    });
  });
});

// ⚠️ 本 describe 会改 MSW 的 mock state（软删标记）→ 必须放最后；vitest 默认按文件内顺序执行、文件间隔离。
describe("反推软删 DELETE /reverse-prompt/jobs/{id}", () => {
  // 🔴 FIX2：上一版这里钉的是「详情仍可取」——**那是我把源码读反了**。我读了 services:276（**列表**的过滤条件）
  // 就推断「列表过滤、详情不过滤」，却没去读详情的取数函数。真实：详情走 reverse_prompt_job_or_404，它用的是
  // **select_live_reverse_prompt_jobs**（services/reverse_prompt.py:365-368）= `deleted_at IS NULL` → **删除后 404**。
  // 后端承重测试逐条钉死（test_reverse_prompt_history.py:262-285：detail / regenerate / save / re-delete 全 404）。
  // 教训：契约测试本该是发现「读错」的地方，但它跟着错的理解一起写，就成了**假绿的放大器**。
  it("软删 → 返 {id, deleted_at}；列表少一条；**详情随即 404**（BE 详情复用 live selector）", async () => {
    const before = await listReversePromptJobs({ source_kind: "video" });
    expect(before.total).toBe(3);
    // 删之前详情可取（对照组，确保下面的 404 是「因为删了」而不是「这条本来就取不到」）
    expect((await getReversePromptJob("rh-vid-3")).id).toBe("rh-vid-3");

    const res = await deleteReversePromptJob("rh-vid-3");
    expect(res.id).toBe("rh-vid-3");
    expect(res.deleted_at).toBeTruthy();

    const after = await listReversePromptJobs({ source_kind: "video" });
    expect(after.total).toBe(2);
    expect(after.items.map((i) => i.id)).toEqual(["rh-vid-1", "rh-vid-2"]);

    // 用户侧的真实后果：列表看不到 **且** 详情打不开 → 界面上没有回头路（故删除文案讲「无法撤销」）。
    await expect(getReversePromptJob("rh-vid-3")).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_JOB_NOT_FOUND"
    });
  });

  // 🔴 FIX2 · 自扫出的第三处宽松：regenerate / save 在 BE 同样经 reverse_prompt_job_or_404 → 已删即 404。
  it("已删记录的 regenerate / save → 404（BE 同走 live selector；旧 mock 恒成功=假绿）", async () => {
    await expect(regenerateReversePrompt("rh-vid-3")).rejects.toMatchObject({ status: 404 });
    await expect(saveReversePrompt("rh-vid-3")).rejects.toMatchObject({ status: 404 });
  });

  it("重复删除 / 不存在 → 404", async () => {
    await expect(deleteReversePromptJob("rh-vid-3")).rejects.toMatchObject({ status: 404 }); // 上条已删
    await expect(deleteReversePromptJob("nope")).rejects.toMatchObject({ status: 404 });
  });
});
