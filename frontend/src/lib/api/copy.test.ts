import { describe, expect, it } from "vitest";

import { rewriteCopy } from "./copy";

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
