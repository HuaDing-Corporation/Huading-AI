import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";

// 坑③：非管理员直达 → 后端 403 → 优雅无权限态，不白屏。
const useAnalyticsOverview = vi.hoisted(() => vi.fn());
const useAnalyticsByTenant = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/hooks", () => ({ useAnalyticsOverview, useAnalyticsByTenant }));
// 隔离编排：子区块 stub 成标记，专测 403 vs 正常分流。
vi.mock("@/components/analytics/date-range-picker", () => ({ DateRangePicker: () => <div data-testid="picker" /> }));
vi.mock("@/components/analytics/overview-cards", () => ({ OverviewCards: () => <div data-testid="overview" /> }));
vi.mock("@/components/analytics/tenant-table", () => ({ TenantTable: () => <div data-testid="tenant" /> }));
vi.mock("@/components/analytics/provider-table", () => ({ ProviderTable: () => <div data-testid="provider" /> }));
vi.mock("@/components/analytics/trend-chart", () => ({ TrendChart: () => <div data-testid="trend" /> }));

import { AnalyticsDashboard, DashboardInner, isForbidden } from "./analytics-dashboard";

const okOverview = { data: { total_credits_used: 1, total_cost_cents: 1, task_count: 1, success_count: 1, failed_count: 0, tenant_count: 1, period: { from: "", to: "" } }, isLoading: false, isError: false, error: null };
const okTenant = { data: { items: [], total: 0 }, isLoading: false, isError: false, error: null };

describe("AnalyticsDashboard (坑③·403 优雅分流)", () => {
  it("isForbidden：仅对 ApiError status=403 为真", () => {
    expect(isForbidden(new ApiError("x", "FORBIDDEN", 403))).toBe(true);
    expect(isForbidden(new ApiError("x", "NOT_FOUND", 404))).toBe(false);
    expect(isForbidden(new Error("x"))).toBe(false);
  });

  it("overview 命中 403 → 无权限态（含返回工作台），四区块均不渲染（承重·不白屏）", async () => {
    useAnalyticsOverview.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new ApiError("Insufficient permission.", "FORBIDDEN", 403) });
    useAnalyticsByTenant.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new ApiError("Insufficient permission.", "FORBIDDEN", 403) });
    render(<AnalyticsDashboard />);
    await waitFor(() => expect(screen.getByText(copy.analytics.forbiddenTitle)).toBeInTheDocument());
    expect(screen.getByText(copy.analytics.forbiddenBack)).toBeInTheDocument();
    expect(screen.queryByTestId("tenant")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider")).not.toBeInTheDocument();
    expect(screen.queryByTestId("trend")).not.toBeInTheDocument();
  });

  it("非法区间(from>to) → overview/reserved 查询 enabled=false 不发请求（承重·避免后端 422）", () => {
    useAnalyticsOverview.mockReturnValue(okOverview);
    useAnalyticsByTenant.mockReturnValue(okTenant);
    render(<DashboardInner range={{ from: "2026-07-10", to: "2026-06-01" }} onRangeChange={vi.fn()} />);
    // overview(range, enabled) 与 by-tenant(range, opts, enabled) 的 enabled 参数须为 false。
    expect(useAnalyticsOverview).toHaveBeenLastCalledWith({ from: "2026-07-10", to: "2026-06-01" }, false);
    expect(useAnalyticsByTenant.mock.calls.at(-1)![2]).toBe(false);
  });

  it("合法区间 → overview/reserved 查询 enabled=true", () => {
    useAnalyticsOverview.mockReturnValue(okOverview);
    useAnalyticsByTenant.mockReturnValue(okTenant);
    render(<DashboardInner range={{ from: "2026-06-01", to: "2026-06-30" }} onRangeChange={vi.fn()} />);
    expect(useAnalyticsOverview).toHaveBeenLastCalledWith({ from: "2026-06-01", to: "2026-06-30" }, true);
    expect(useAnalyticsByTenant.mock.calls.at(-1)![2]).toBe(true);
  });

  it("管理员 200 → 四区块正常渲染，无无权限态", async () => {
    useAnalyticsOverview.mockReturnValue(okOverview);
    useAnalyticsByTenant.mockReturnValue(okTenant);
    render(<AnalyticsDashboard />);
    await waitFor(() => expect(screen.getByTestId("overview")).toBeInTheDocument());
    expect(screen.getByTestId("tenant")).toBeInTheDocument();
    expect(screen.getByTestId("provider")).toBeInTheDocument();
    expect(screen.getByTestId("trend")).toBeInTheDocument();
    expect(screen.queryByText(copy.analytics.forbiddenTitle)).not.toBeInTheDocument();
  });
});
