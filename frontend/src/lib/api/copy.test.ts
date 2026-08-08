import { describe, expect, it } from "vitest";

import { generateTitles, generateTopics, rewriteCopy } from "./copy";

// ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX3（CB P2-1 · 机制按"接受方"算）：/copy/rewrite 的 CopyRewriteRequest
// 也接受 duration_sec（前端类型 + BE copy.py 皆 int|None）。此前 mock 没读该字段 → duration_sec:5.5 被接受、真 BE 会 422，
// 「将来任何路径用上这个端点就假绿」。本测真走 apiFetch→全局 MSW：整数/省略正常、小数与字符串 5.5 → 422。
describe("rewriteCopy · POST /copy/rewrite（apiFetch 真走 MSW · FIX3 机制）", () => {
  it("合法整数 duration_sec(30) / 省略 → 正常返回 results", async () => {
    const withDur = await rewriteCopy({ source_text: "原文案", mode: "smart", duration_sec: 30 });
    expect(withDur.results.length).toBeGreaterThan(0);
    const noDur = await rewriteCopy({ source_text: "原文案", mode: "smart" });
    expect(noDur.results.length).toBeGreaterThan(0);
  });

  it("防假绿：小数 duration_sec(5.5) → 422（BE int，mock 现拒非整数）", async () => {
    await expect(rewriteCopy({ source_text: "原文案", mode: "smart", duration_sec: 5.5 })).rejects.toThrow();
  });

  it("防假绿：字符串 duration_sec('5.5') → 422", async () => {
    await expect(
      // @ts-expect-error 故意传非法类型：真 BE int 也拒字符串，mock 须同样 422
      rewriteCopy({ source_text: "原文案", mode: "smart", duration_sec: "5.5" })
    ).rejects.toThrow();
  });
});

/**
 * PRICING-UI-0001-FIX1 · BE PR #239 `e2bc2c02` 给三个文案端点的成功响应加了**必填** `outcome`
 *（`schemas/copy.py` 的 `CopyGenerationOutcome`，`operation` 是 Literal["rewrite","titles","topics"]）。
 * mock 不返它就比 BE 松：前端类型声明了必填字段而 mock 给不出，任何将来消费它的代码都会在
 * 真接口下才炸。
 *
 * 🔴 同时钉住 `operation` **不是三个端点共用一个值** —— 若 mock 图省事三处都写 "rewrite"，
 *    这条会红（那种复制粘贴错误在 mock 里最常见，且真接口下不会发生，属于典型的"只在 mock 里骗人"）。
 * ⚠️ 前端**不拿 outcome 当成败主判据**（理由见 types.ts 的注释：网络中断时根本没有响应体）。
 *    本组门守的是"契约字段在场且取值正确"，不是"前端据它判成败"。
 */
describe("文案端点的 outcome（BE #239 必填字段，mock 不比 BE 松）", () => {
  it("三个端点的成功响应各带自己的 outcome：operation 各不相同、status 均 succeeded", async () => {
    const rw = await rewriteCopy({ source_text: "原文案", mode: "smart" });
    const ti = await generateTitles({ source_text: "原文案" });
    const to = await generateTopics({ source_text: "原文案" });

    expect(rw.outcome).toEqual({ operation: "rewrite", status: "succeeded" });
    expect(ti.outcome).toEqual({ operation: "titles", status: "succeeded" });
    expect(to.outcome).toEqual({ operation: "topics", status: "succeeded" });
    // 三者互不相同（防"三处都写同一个 operation"的复制粘贴）。
    expect(new Set([rw.outcome.operation, ti.outcome.operation, to.outcome.operation]).size).toBe(3);
  });

  it("auto 多版分支同样带 outcome（不是只在 smart 分支加了）", async () => {
    const auto = await rewriteCopy({ source_text: "原文案", mode: "auto", n: 3 });
    expect(auto.results).toHaveLength(3);
    expect(auto.outcome).toEqual({ operation: "rewrite", status: "succeeded" });
  });
});
