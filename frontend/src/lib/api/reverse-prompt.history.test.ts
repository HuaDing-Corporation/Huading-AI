import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  deleteReversePromptJob,
  getReversePromptJob,
  listReversePromptJobs,
  regenerateReversePrompt,
  reverseFromAsset,
  saveReversePrompt,
  type ReverseSourceKind
} from "@/lib/api/reverse-prompt";
import { resetReverseJobs } from "@/mocks/handlers";

// HISTORY-VIDEO-REVERSE-UI-0001 · 反推历史契约承重（真打 MSW /reverse-prompt/jobs，不 mock adapter）。
// 逐字段对齐**已合入 develop 的真 BE**（非冻结文档摘要）：
//   backend/app/schemas/reverse_prompt.py:92-110（列表项 6 字段 / 列表响应 / 软删响应）
//   backend/app/api/v1/routes/reverse_prompt.py:64-87（source_kind Literal + page/page_size 上下界）、:152-166（软删）
//   backend/app/services/reverse_prompt.py:276（列表过滤软删）、:320-322（video 无缩略图）、:332-339（summary 截断）
// 全确定值断言；期望值手写、不 import 被测代码的常量。
//
// 🔴 FIX4：每条测试前**重置 mock job store**。上一版靠「把改状态的 describe 放最后」维持互不干扰 ——
// 那是排座位，不是机制：Codex B 单独跑 ALREADY_RUNNING 那条即 `1 failed / 24 skipped`，而 shuffle
// 一开更是 10 条红。重置之后每条测试都从同一份 seed 出发，顺序无关。
beforeEach(() => {
  localStorage.clear();
  resetReverseJobs();
});
afterEach(() => localStorage.clear());

describe("反推历史列表 GET /reverse-prompt/jobs", () => {
  // REVERSE-DEEP-UI-0001：mock 新增第 7 条 `rh-img-legacy`（形态②**老结构结果**，供「回落」路径真测）。
  // REVERSE-ZH-MOCK-SYNC-UI-0001：再新增第 8 条 `rh-img-zh-degraded`（形态④**中文降级结果**，
  //   供 BE #225 的 `[中文缺失，以下为英文原文]` 降级支真测）。
  // 故「全部」8 条、image 5 条；两条新 seed 的 created_at 最早 → 倒序排最后。video 侧不受影响（仍 3 条）。
  it("省略 source_kind = 全部：8 条（image 5 + video 3），按 created_at 倒序；分页默认 page=1/page_size=20", async () => {
    const r = await listReversePromptJobs();
    expect(r.total).toBe(8);
    expect(r.page).toBe(1);
    expect(r.page_size).toBe(20); // BE routes:75 默认 20
    expect(r.items.map((i) => i.id)).toEqual([
      "rh-img-1",
      "rh-img-2",
      "rh-img-3",
      "rh-vid-1",
      "rh-vid-2",
      "rh-vid-3",
      "rh-img-legacy",
      "rh-img-zh-degraded"
    ]);
  });

  it("source_kind=image：恰 5 条，且都有 source_thumbnail_url", async () => {
    const r = await listReversePromptJobs({ source_kind: "image" });
    expect(r.total).toBe(5);
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
    // 末两位是新增的老结构 seed 与中文降级 seed（都是 succeeded）——见上方「全部 8 条」注释。
    expect(r.items.map((i) => i.status)).toEqual([
      "succeeded",
      "saved",
      "failed",
      "succeeded",
      "running",
      "queued",
      "succeeded",
      "succeeded"
    ]);
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
    expect(r.total).toBe(8); // 含新增的老结构 seed + 中文降级 seed（见上方注释）
  });
});

describe("反推详情 GET /reverse-prompt/jobs/{id}", () => {
  // 🔴 FIX2 真联调：键集合从 6 键改 **5 键**。上一版这条把 `ecom_poster` 钉成了「正确答案」——
  //    它是本项目最硬的那类假绿：**严格相等**断言把一个 BE 永不返回的键锁进了「契约」，对真 BE 必红。
  //    BE 侧同款断言：backend/tests/test_reverse_prompt_pipeline.py:2713。
  it("succeeded(image)：含 result + fill_targets 5 键；video_analysis 为 null（图片源）", async () => {
    const job = await getReversePromptJob("rh-img-1");
    expect(job.status).toBe("succeeded");
    expect(job.source_kind).toBe("image");
    expect(job.result?.prompt_zh).toContain("保温杯");
    expect(job.result?.prompt_en).toContain("insulated bottle");
    expect(Object.keys(job.result!.fill_targets).sort()).toEqual([
      "avatar_talk",
      "ecom_model",
      "photo",
      "seedance_i2v",
      "video_gen"
    ]);
    expect(job.result?.video_analysis ?? null).toBeNull();
  });

  // ══ REVERSE-ZH-MOCK-SYNC-UI-0001 · 承重门 1–3（BE #225 §九 v3：structured_prompt.zh 改真中文）══
  //
  // 背景：#220 FIX2 时我把 mock 的 `.zh` 校准成「与 `.en` 共用同一组 value、只换中文标签」——
  // 那时 BE 确实那么产出。#225 之后 `.zh` 的**值**由模型原生返回中文，mock 反而不忠实了。

  /**
   * 承重门1：`.zh` 的值是**中文**，且与 `.en` 的对应值**不同**。
   * 🔴 判据用 CJK 码位（与 BE 的 `_contains_han` 同一判据，services:834-841），不是「含某个特定词」——
   *    后者换个 fixture 就失效，前者钉的是「这一栏必须是中文」这条契约本身。
   * 变异：把 handlers.ts 的 STRUCTURED_ZH 改回英文值（即 FIX2 那版）→ 本条必红。
   */
  it("🔴 承重门1 · structured_prompt.zh 的值是中文，且不等于 .en 的值", async () => {
    const sp = (await getReversePromptJob("rh-img-1")).result!.structured_prompt!;
    const HAN = /[㐀-䶿一-鿿]/;

    // 逐行拆开断言：整串含汉字太松（中文标签本身就是汉字，值全英文也能过）。
    const zhLines = sp.zh.split("\n");
    const enLines = sp.en.split("\n");
    expect(zhLines).toHaveLength(7); // 7 个固定小节（services:801-814）
    expect(enLines).toHaveLength(7);

    for (const [i, line] of zhLines.entries()) {
      // 标签仍是 **ASCII 冒号 + 一个空格**（f"{label}: {…}"，services:821）——不是全角「：」
      const [label, ...rest] = line.split(": ");
      const value = rest.join(": ");
      expect(label).toMatch(HAN); // 中文标签
      expect(value, `第 ${i + 1} 行的值应为中文：${line}`).toMatch(HAN); // 🔴 **值**也必须是中文
      // 与 .en 同位置的值逐行比对：必须不同（#225 前它们是同一组串）
      expect(value).not.toBe(enLines[i].split(": ").slice(1).join(": "));
    }
    expect(sp.zh).not.toBe(sp.en);
  });

  /**
   * 承重门2：降级形态的前缀**逐字**等于 BE 的 `_STRUCTURED_ZH_FALLBACK_PREFIX`
   *（backend/app/services/reverse_prompt.py:44）。
   * 🔴 前缀与英文原文之间是**一个半角空格**（`f"{PREFIX} {english_value}"`，services:831）。
   * 🔴 降级是**逐字段**的：同一份结果里可以有的行降级、有的行正常 —— 只准备「全降级」样本会漏掉
   *    真实里最常见的半降级形态。
   * 变异：改动前缀任一个字（含标点/空格）、或去掉方括号把降级行伪装成正常行 → 本条必红。
   */
  it("🔴 承重门2 · 中文缺失降级：前缀逐字为「[中文缺失，以下为英文原文] 」且逐字段生效", async () => {
    const sp = (await getReversePromptJob("rh-img-zh-degraded")).result!.structured_prompt!;
    const PREFIX = "[中文缺失，以下为英文原文]";
    const HAN = /[㐀-䶿一-鿿]/;
    const zhLines = sp.zh.split("\n");
    const enLines = sp.en.split("\n");
    const valueOf = (line: string) => line.split(": ").slice(1).join(": ");

    // 🔴 判据是**穷举每一行**，不是「至少有一行降级」。
    //    第一版我写的是 `filter(l => l.includes(PREFIX)).length > 0` —— 变异检验时它**抓不住**
    //    「把某一行的前缀改错一个字」：那行只是从 degraded 桶掉进 normal 桶，两个 length 都还 > 0，测试照绿。
    //    现在按 BE 的真实产出穷举：`_structured_zh_value`（services:827-831）只会产出两种值 ——
    //      (a) 模型给的**纯中文**；(b) `[前缀] <英文原文>`（前缀后一个半角空格）。
    //    于是每行只有两条合法路：以 `[` 开头就必须逐字是 (b)；否则必须是 (a)。
    let degradedCount = 0;
    let normalCount = 0;
    for (const [idx, line] of zhLines.entries()) {
      const value = valueOf(line);
      const enValue = valueOf(enLines[idx]);
      if (value.startsWith("[")) {
        // 任何「看起来像降级」的行，前缀 + 空格 + 英文原文都必须**逐字**对上（改一个字即红）
        expect(value, `第 ${idx + 1} 行降级形态应逐字匹配 BE 的 _STRUCTURED_ZH_FALLBACK_PREFIX`).toBe(
          `${PREFIX} ${enValue}`
        );
        degradedCount += 1;
      } else {
        expect(value, `第 ${idx + 1} 行应为纯中文值`).toMatch(HAN);
        // 🔴 正常行不许夹带英文原文 —— 否则「去掉方括号」就能把一个降级行伪装成正常行蒙混过关
        //   （它仍含「中文缺失」等汉字，只靠 HAN 判据是抓不住的；这条是变异 2b 逼出来的）。
        expect(value, `第 ${idx + 1} 行是正常中文行，不该夹带 .en 的英文原文`).not.toContain(enValue);
        normalCount += 1;
      }
    }
    // 逐字段降级：两种行都得有（全降级或全不降级都说明样本没覆盖到这个语义）
    expect(degradedCount).toBeGreaterThan(0);
    expect(normalCount).toBeGreaterThan(0);
  });

  /**
   * 承重门3：`.en` 与带入取值**零变化** —— 中文降级绝不能影响带入。
   * BE 侧的根据：降级只发生在 `_structured_zh_value`（services:827-831），它只参与 `.zh` 的拼装；
   * fill_targets 取的一直是 `structured_en`（services:737/754/769/780）。
   * 取证已确认：正常态与降级态的 `.en` 逐字相同。
   */
  it("🔴 承重门3 · 中文降级不影响 .en 与带入：三处取值仍逐字等于 structured_prompt.en", async () => {
    const normal = (await getReversePromptJob("rh-img-1")).result!;
    const degraded = (await getReversePromptJob("rh-img-zh-degraded")).result!;

    // .en 在两种形态下逐字相同（降级只碰 .zh）
    expect(degraded.structured_prompt!.en).toBe(normal.structured_prompt!.en);

    // 带入取值仍是 .en（BE services:737/754/769/780）——降级态下也一样
    for (const r of [normal, degraded]) {
      const en = r.structured_prompt!.en;
      expect(r.fill_targets.video_gen.prompt).toBe(en);
      expect(r.fill_targets.photo.topic).toBe(en);
      expect(r.fill_targets.seedance_i2v.scene_prompt).toBe(en);
      expect(r.fill_targets.ecom_model.extra_prompt).toBe(en);
    }
    // 整个 fill_targets 在两形态间逐字相同 —— 「降级不改带入」的最强表述
    expect(degraded.fill_targets).toEqual(normal.fill_targets);
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

// 🔴 FIX4：这里原本写着「⚠️ 本 describe 会改 mock state → **必须放最后**」——
// 那句话是**把顺序依赖制度化**：它承认状态会泄漏，然后要求所有人记住座位表。已改为 beforeEach 重置 store，
// 本 describe 放哪都行（shuffle 实证）。
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
  // 🔴 FIX4：这两条原本靠**上一条测试**先把 rh-vid-3 删掉 —— 与 Codex B 抓的那条同病，只是没人单独跑过。
  // store 一重置它俩就红了（顺序执行和 shuffle 下同样红）→ 证明它们从来就没自己建立过前提。现在自己删。
  it("已删记录的 regenerate / save → 404（BE 同走 live selector；旧 mock 恒成功=假绿）", async () => {
    await deleteReversePromptJob("rh-vid-3"); // 前提由本条自己建立
    await expect(regenerateReversePrompt("rh-vid-3")).rejects.toMatchObject({ status: 404 });
    await expect(saveReversePrompt("rh-vid-3")).rejects.toMatchObject({ status: 404 });
  });

  it("重复删除 / 不存在 → 404", async () => {
    await deleteReversePromptJob("rh-vid-3"); // 第一次成功
    await expect(deleteReversePromptJob("rh-vid-3")).rejects.toMatchObject({ status: 404 }); // 第二次 → 404
    await expect(deleteReversePromptJob("nope")).rejects.toMatchObject({ status: 404 });
  });
});

// ── 🔴 FIX3：单一权威 job store 的承重 ────────────────────────────────────────────────
// Codex B 的结论：剩下的不是「第五处遗漏」，是**没有权威存储 → 每个 handler 各自猜「什么该成功」**。
// 下面每条都钉「必须先存在于 store」这件事本身 —— 摘掉 liveJob 守卫 → 对应条必红。
describe("反推 job store（单一权威存储）", () => {
  it("🔴 不存在的 rp-*（旧 mock 的放行分支）→ 404，不再凭 id 前缀现编一份 job", async () => {
    // 旧 mock：`if (id.startsWith("rp-")) return ok(reverseJobRead(id))` —— 任何 rp-* 都返 200 假 job。
    // 那不是疏忽：POST 图片反推产生的 job 当时根本没落地，除了现编无路可走。
    await expect(getReversePromptJob("rp-99999")).rejects.toMatchObject({
      status: 404,
      code: "REVERSE_PROMPT_JOB_NOT_FOUND"
    });
    await expect(regenerateReversePrompt("rp-99999")).rejects.toMatchObject({ status: 404 });
    await expect(saveReversePrompt("rp-99999")).rejects.toMatchObject({ status: 404 });
  });

  it("🔴 不存在的任意 ID（含 rpv-* / 裸串）→ 404（四个动作口径一致，不是各 handler 各判）", async () => {
    for (const id of ["rpv-99999", "bogus", "rh-nope"]) {
      await expect(getReversePromptJob(id)).rejects.toMatchObject({ status: 404 });
      await expect(regenerateReversePrompt(id)).rejects.toMatchObject({ status: 404 });
      await expect(saveReversePrompt(id)).rejects.toMatchObject({ status: 404 });
      await expect(deleteReversePromptJob(id)).rejects.toMatchObject({ status: 404 });
    }
  });

  // 🔴 BE services/reverse_prompt.py:252-257 —— `if job.status not in {"succeeded", "saved"}` → 409。
  // 旧 mock 对 queued/running/failed 一律成功 = 与 BE **正相反**（线上必 409、本地全绿）。
  it("🔴 save 非终态 → 409 REVERSE_PROMPT_NOT_READY（queued / running / failed 都不行）", async () => {
    // queued：现造一个视频 job（BE POST 视频源 → 202 queued）。**不用 rh-vid-3 那个 queued seed** ——
    // 它被上面的软删 describe 删了，拿它测会得到 404 而不是 409，等于又赌一次执行顺序（刚在 rp-1 修掉的病）。
    const queued = await reverseFromAsset({ source_asset_id: "video-asset-1" });
    expect(queued.status).toBe("queued");
    await expect(saveReversePrompt(queued.id)).rejects.toMatchObject({
      status: 409,
      code: "REVERSE_PROMPT_NOT_READY"
    });
    await expect(saveReversePrompt("rh-vid-2")).rejects.toMatchObject({ status: 409 }); // running
    await expect(saveReversePrompt("rh-img-3")).rejects.toMatchObject({ status: 409 }); // failed
  });

  // BE 放行的是**两个**终态：`not in {"succeeded","saved"}` → saved 也放行 → 重复保存幂等。
  // （任务包只提了 succeeded；以源码为准。）
  it("save 终态放行：succeeded → saved；已 saved 再保存**仍成功**（BE 放行 {succeeded, saved} 两态，幂等）", async () => {
    const first = await saveReversePrompt("rh-img-1"); // succeeded
    expect(first.status).toBe("saved");
    const again = await saveReversePrompt("rh-img-1"); // 已 saved → BE 仍放行
    expect(again.status).toBe("saved");
    const seeded = await saveReversePrompt("rh-img-2"); // seed 本就是 saved
    expect(seeded.status).toBe("saved");
  });

  // 🔴 BE routes:183-185 —— source_kind=="video" → `_enqueue_reverse_prompt_video` + **202**；
  // services:220 → status 重置为 **queued**、result_json=None。
  // 旧 mock 把视频 job 的 regenerate 伪造成 **image succeeded / 200** —— 源类型和同步性一起造假。
  it("🔴 视频 job regenerate → 202 + 重新 queued（不是 image succeeded / 200）", async () => {
    const before = await getReversePromptJob("rh-vid-1");
    expect(before.status).toBe("succeeded");
    expect(before.source_kind).toBe("video");

    const job = await regenerateReversePrompt("rh-vid-1");
    expect(job.status).toBe("queued"); // 不是 succeeded
    expect(job.source_kind).toBe("video"); // 不是 image
    expect(job.result ?? null).toBeNull(); // BE 清 result_json
  });

  // 🔴 这条**任务包的表里没有** —— 我读 BE 时发现的：prepare_reverse_prompt_video_retry:197-202
  // 对 queued/running 的视频 job 直接 409 ALREADY_RUNNING（旧 mock 对它照样返成功）。
  //
  // 🔴 FIX4：本条上一版的 queued 样本靠「上一条测试刚把 rh-vid-1 打回 queued」—— 我甚至把这句依赖
  // **写进了注释**，就在我认「赌执行顺序」这个病的下一条。隔离跑即 1 failed（接口返 queued 成功，不是 409）。
  // 现在两个样本都在本条内自建：running 用重置后确定为 running 的 seed，queued 由本条自己打回。
  it("🔴 视频 job 正在跑（queued/running）时 regenerate → 409 REVERSE_PROMPT_ALREADY_RUNNING", async () => {
    // running：store 每条测试前重置 → rh-vid-2 确定是 running，不依赖谁先跑
    await expect(regenerateReversePrompt("rh-vid-2")).rejects.toMatchObject({
      status: 409,
      code: "REVERSE_PROMPT_ALREADY_RUNNING"
    });

    // queued：**本条自己**把 rh-vid-1（succeeded）打回 queued —— 前提由自己建立，不借别的测试
    expect((await regenerateReversePrompt("rh-vid-1")).status).toBe("queued");
    await expect(regenerateReversePrompt("rh-vid-1")).rejects.toMatchObject({
      status: 409,
      code: "REVERSE_PROMPT_ALREADY_RUNNING"
    });
  });

  it("POST 创建的 job 真的落进 store：新 job 的 id 可取详情、可 save（不再靠 id 前缀猜）", async () => {
    const created = await reverseFromAsset({ source_asset_id: "upload-1" });
    expect((await getReversePromptJob(created.id)).id).toBe(created.id); // 存在 → 可取
    expect((await saveReversePrompt(created.id)).status).toBe("saved"); // succeeded → 可存
  });
});
