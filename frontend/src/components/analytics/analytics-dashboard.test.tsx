import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";

// 坑③：非管理员直达 → 后端 403 → 优雅无权限态，不白屏。
const useAnalyticsOverview = vi.hoisted(() => vi.fn());
const useAnalyticsByTenant = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/hooks", () => ({ useAnalyticsOverview, useAnalyticsByTenant }));

// PROD-P0-ANALYTICS-TENANT-LEAK：看板据 permissions 分平台/VIP 视图（隐藏「用户排行」）——注入可控 session。
const authMock = vi.hoisted(() => ({ session: { user: { permissions: [] as string[] } }, ready: true }));
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => authMock }));
const setPermissions = (permissions: string[]) => {
  authMock.session = { user: { permissions } };
};
// 默认平台方（含 analytics_platform）→ 全站视图，保既有断言。
beforeEach(() => setPermissions(["analytics_view", "analytics_platform"]));
// 隔离编排：子区块 stub 成标记，专测 403 vs 正常分流。
vi.mock("@/components/analytics/date-range-picker", () => ({ DateRangePicker: () => <div data-testid="picker" /> }));
vi.mock("@/components/analytics/overview-cards", () => ({ OverviewCards: () => <div data-testid="overview" /> }));
vi.mock("@/components/analytics/tenant-table", () => ({ TenantTable: () => <div data-testid="tenant" /> }));
vi.mock("@/components/analytics/provider-table", () => ({ ProviderTable: () => <div data-testid="provider" /> }));
vi.mock("@/components/analytics/trend-chart", () => ({ TrendChart: () => <div data-testid="trend" /> }));

import { AnalyticsDashboard, DashboardInner, isForbidden, isPlanRequired } from "./analytics-dashboard";

const okOverview = { data: { total_credits_used: 1, total_cost_cents: 1, task_count: 1, success_count: 1, failed_count: 0, tenant_count: 1, period: { from: "", to: "" } }, isLoading: false, isError: false, error: null };
const okTenant = { data: { items: [], total: 0 }, isLoading: false, isError: false, error: null };

describe("AnalyticsDashboard (VIP 门禁·403 优雅分流 · ADMIN-VIP-GATE-UI-0001)", () => {
  it("isPlanRequired：仅对 ApiError code=ANALYTICS_PLAN_REQUIRED 为真；isForbidden：任意 403 为真", () => {
    expect(isPlanRequired(new ApiError("x", "ANALYTICS_PLAN_REQUIRED", 403))).toBe(true);
    expect(isPlanRequired(new ApiError("x", "FORBIDDEN", 403))).toBe(false);
    expect(isPlanRequired(new Error("x"))).toBe(false);
    expect(isForbidden(new ApiError("x", "FORBIDDEN", 403))).toBe(true);
    expect(isForbidden(new ApiError("x", "NOT_FOUND", 404))).toBe(false);
  });

  it("overview 命中 403 ANALYTICS_PLAN_REQUIRED → VIP 友好页「仅 huading plan 用户可查看」，四区块均不渲染（承重·不白屏/不透传 403）", async () => {
    const planErr = new ApiError("Analytics requires the huading plan.", "ANALYTICS_PLAN_REQUIRED", 403);
    useAnalyticsOverview.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: planErr });
    useAnalyticsByTenant.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: planErr });
    render(<AnalyticsDashboard />);
    await waitFor(() => expect(screen.getByText(copy.analytics.planRequiredTitle)).toBeInTheDocument());
    expect(screen.getByText(copy.analytics.planRequiredBack)).toBeInTheDocument();
    // 不透传后端英文原串。
    expect(screen.queryByText(/huading plan\.$/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("tenant")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider")).not.toBeInTheDocument();
    expect(screen.queryByTestId("trend")).not.toBeInTheDocument();
  });

  it("兜底：任意 403（未带 code）也走同一 VIP 友好页（不白屏）", async () => {
    const bareForbidden = new ApiError("Insufficient permission.", "FORBIDDEN", 403);
    useAnalyticsOverview.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: bareForbidden });
    useAnalyticsByTenant.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: bareForbidden });
    render(<AnalyticsDashboard />);
    await waitFor(() => expect(screen.getByText(copy.analytics.planRequiredTitle)).toBeInTheDocument());
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

  it("平台方（analytics_platform）200 → 四区块全渲染（含「用户排行」），无无权限态", async () => {
    setPermissions(["analytics_view", "analytics_platform"]);
    useAnalyticsOverview.mockReturnValue(okOverview);
    useAnalyticsByTenant.mockReturnValue(okTenant);
    render(<AnalyticsDashboard />);
    await waitFor(() => expect(screen.getByTestId("overview")).toBeInTheDocument());
    expect(screen.getByTestId("tenant")).toBeInTheDocument(); // 用户排行仅平台方可见
    expect(screen.getByTestId("provider")).toBeInTheDocument();
    expect(screen.getByTestId("trend")).toBeInTheDocument();
    expect(screen.queryByText(copy.analytics.planRequiredTitle)).not.toBeInTheDocument();
  });

  // 承重·P0 防泄漏：VIP 客户（有 analytics_view 无 analytics_platform）→ **隐藏「用户排行」**，其余区块正常。
  it("VIP 客户（无 analytics_platform）200 → 隐藏「用户排行」TenantTable，overview/provider/trend 仍渲染", async () => {
    setPermissions(["analytics_view"]); // 只有 view，无 platform
    useAnalyticsOverview.mockReturnValue(okOverview);
    useAnalyticsByTenant.mockReturnValue(okTenant);
    render(<AnalyticsDashboard />);
    await waitFor(() => expect(screen.getByTestId("overview")).toBeInTheDocument());
    expect(screen.queryByTestId("tenant")).not.toBeInTheDocument(); // 🔴 不得出现枚举全站租户的用户排行
    expect(screen.getByTestId("provider")).toBeInTheDocument();
    expect(screen.getByTestId("trend")).toBeInTheDocument();
    expect(screen.queryByText(copy.analytics.planRequiredTitle)).not.toBeInTheDocument();
  });
});
