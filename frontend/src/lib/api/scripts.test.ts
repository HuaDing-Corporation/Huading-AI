import { describe, expect, it } from "vitest";

import { generateScript } from "./scripts";

// ECOM-VIDEO-SCENE-DURATION-FIX-UI-0001 · FIX2（CB P1 · 机制）：「AI生成文案」路径此前漏 isValidDuration 门，真发
// {duration_sec:5.5} 到 BE，而 BE ScriptGenerateRequest.duration_sec 是 int → 422；scripts mock 又没校验该字段 = 假绿。
// 本测真走 apiFetch→全局 MSW：整数正常、小数 422——让任何未加门的发送路径在测试里响亮失败，而非悄悄假绿。
describe("generateScript · POST /scripts/generate（apiFetch 真走 MSW · FIX2 机制）", () => {
  it("合法整数 duration_sec(10) → 正常返回 script", async () => {
    const res = await generateScript({ topic: "保温杯", video_mode: "seedance_i2v", duration_sec: 10, length_tier: "medium" });
    expect(res.script).toBeTruthy();
  });

  it("防假绿：小数 duration_sec(5.5) → 422（BE int，scripts mock 现拒非整数）", async () => {
    await expect(generateScript({ topic: "保温杯", duration_sec: 5.5 })).rejects.toThrow();
  });
});
