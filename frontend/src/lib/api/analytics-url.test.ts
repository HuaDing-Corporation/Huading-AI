import { describe, expect, it } from "vitest";

// 坑②真验证：不 mock client，直接对真实 apiUrl()（承担双前缀守卫的函数）断言 analytics 路径单前缀。
// analytics.test.ts 里 client 被 mock，那处 not.toContain("/api/api") 只是对 fetcher 路径字面量的自证；
// 双前缀风险真实存在于 apiUrl（base 以 /api 结尾时的拼接），此处补真函数覆盖。
import { apiUrl } from "./client";

const PATHS = [
  "/api/v1/admin/analytics/overview",
  "/api/v1/admin/analytics/by-tenant",
  "/api/v1/admin/analytics/by-provider",
  "/api/v1/admin/analytics/timeseries"
];

describe("apiUrl · analytics 路径单前缀（坑②真守卫）", () => {
  for (const p of PATHS) {
    it(`${p} → 无 /api/api 双前缀，且保留 /api/v1/admin/analytics/`, () => {
      const url = apiUrl(p);
      expect(url).not.toContain("/api/api");
      expect(url).toContain("/api/v1/admin/analytics/");
    });
  }
});
