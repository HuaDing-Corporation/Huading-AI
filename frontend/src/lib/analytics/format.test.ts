import { describe, expect, it } from "vitest";

import { credits, intFmt, pct, sharePct, yuan } from "./format";

describe("analytics format helpers (口径)", () => {
  it("yuan：分 → 元，¥ 前缀 + 千分位 + 两位小数（cost_cents/100）", () => {
    expect(yuan(210400)).toBe("¥2,104.00");
    expect(yuan(0)).toBe("¥0.00");
    expect(yuan(1892340)).toBe("¥18,923.40");
  });

  it("pct：success_rate 0–1 → 百分比 1 位小数（×100）", () => {
    expect(pct(0.965)).toBe("96.5%");
    expect(pct(1)).toBe("100.0%");
    expect(pct(0)).toBe("0.0%");
  });

  it("sharePct：share_pct 已是百分比数值 → 不再 ×100", () => {
    expect(sharePct(44.6)).toBe("44.6%");
    expect(sharePct(9.8)).toBe("9.8%");
  });

  it("intFmt：整数千分位（任务量/计费笔数/租户数）", () => {
    expect(intFmt(5230)).toBe("5,230");
    expect(intFmt(46)).toBe("46");
  });

  it("credits：浮点保留 ≤1 位小数、去尾零、千分位", () => {
    expect(credits(48213.5)).toBe("48,213.5");
    expect(credits(1200)).toBe("1,200"); // 去尾零
    expect(credits(6722)).toBe("6,722");
  });
});
