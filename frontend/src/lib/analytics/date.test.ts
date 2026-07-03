import { describe, expect, it } from "vitest";

import { lastNDaysRange, shortDate, ymd } from "./date";

describe("analytics date helpers", () => {
  it("ymd：本地年月日零填充（不受 UTC 偏移把日期挪前一天）", () => {
    expect(ymd(new Date(2026, 5, 3))).toBe("2026-06-03"); // 月份 0-based
    expect(ymd(new Date(2026, 11, 31))).toBe("2026-12-31");
  });

  it("lastNDaysRange(30)：from = to - 29 天（含今天，共 30 天，与后端一致）", () => {
    const r = lastNDaysRange(30, new Date(2026, 5, 30)); // 2026-06-30
    expect(r.to).toBe("2026-06-30");
    expect(r.from).toBe("2026-06-01");
  });

  it("lastNDaysRange(7)：跨月正确", () => {
    const r = lastNDaysRange(7, new Date(2026, 6, 3)); // 2026-07-03
    expect(r.to).toBe("2026-07-03");
    expect(r.from).toBe("2026-06-27");
  });

  it("shortDate：YYYY-MM-DD → MM-DD", () => {
    expect(shortDate("2026-06-03")).toBe("06-03");
  });
});
