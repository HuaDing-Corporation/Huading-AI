import { beforeEach, describe, expect, it, vi } from "vitest";

// 坑②：API 必须单前缀 /api/v1/admin/analytics/*，严禁 /api/api double-prefix。
const apiFetch = vi.hoisted(() => vi.fn<(path: string) => Promise<unknown>>(() => Promise.resolve({})));
vi.mock("@/lib/api/client", () => ({ apiFetch }));

import { fetchAnalyticsByProvider, fetchAnalyticsByTenant, fetchAnalyticsOverview, fetchAnalyticsTimeseries } from "./analytics";

const range = { from: "2026-06-01", to: "2026-06-30" };
const lastPath = () => apiFetch.mock.calls.at(-1)![0];

beforeEach(() => apiFetch.mockClear());

describe("analytics fetchers (单前缀 + 参数)", () => {
  it("overview：/api/v1/admin/analytics/overview + from/to，不含 /api/api（承重·坑②）", async () => {
    await fetchAnalyticsOverview(range);
    const path = lastPath();
    expect(path).toMatch(/^\/api\/v1\/admin\/analytics\/overview\?/);
    expect(path).not.toContain("/api/api");
    expect(path).toContain("from=2026-06-01");
    expect(path).toContain("to=2026-06-30");
  });

  it("by-tenant：带 sort/limit/offset，单前缀", async () => {
    await fetchAnalyticsByTenant(range, { sort: "cost_asc", limit: 20, offset: 40 });
    const path = lastPath();
    expect(path).toMatch(/^\/api\/v1\/admin\/analytics\/by-tenant\?/);
    expect(path).not.toContain("/api/api");
    expect(path).toContain("sort=cost_asc");
    expect(path).toContain("limit=20");
    expect(path).toContain("offset=40");
  });

  it("by-provider：单前缀 + from/to", async () => {
    await fetchAnalyticsByProvider(range);
    const path = lastPath();
    expect(path).toMatch(/^\/api\/v1\/admin\/analytics\/by-provider\?/);
    expect(path).not.toContain("/api/api");
  });

  it("timeseries：带 granularity=week，单前缀", async () => {
    await fetchAnalyticsTimeseries(range, "week");
    const path = lastPath();
    expect(path).toMatch(/^\/api\/v1\/admin\/analytics\/timeseries\?/);
    expect(path).not.toContain("/api/api");
    expect(path).toContain("granularity=week");
  });
});
