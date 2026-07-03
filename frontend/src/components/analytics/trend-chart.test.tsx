import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const useAnalyticsTimeseries = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/hooks", () => ({ useAnalyticsTimeseries }));

import { TrendChart } from "./trend-chart";

const range = { from: "2026-06-01", to: "2026-06-30" };
const lastGranularity = () => useAnalyticsTimeseries.mock.calls.at(-1)![1] as string;

beforeEach(() => {
  useAnalyticsTimeseries.mockReset();
  useAnalyticsTimeseries.mockReturnValue({
    data: {
      buckets: [
        { date: "2026-06-01", credits_used: 100, cost_cents: 5000, task_count: 10 },
        { date: "2026-06-02", credits_used: 220, cost_cents: 8000, task_count: 18 }
      ]
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn()
  });
});

describe("TrendChart (day/week 切换 + 指标)", () => {
  it("默认 day 粒度；点「按周」→ granularity=week（承重·day/week 切换）", () => {
    render(<TrendChart range={range} />);
    expect(lastGranularity()).toBe("day");
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.granWeek }));
    expect(lastGranularity()).toBe("week");
  });

  it("渲染 SVG 图（role=img）+ 屏幕阅读器摘要", () => {
    render(<TrendChart range={range} />);
    expect(screen.getByRole("img")).toBeInTheDocument();
  });

  it("指标切到「成本」→ Y 轴出现 ¥ 刻度（cost_cents/100 口径）", () => {
    render(<TrendChart range={range} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.metricCost }));
    // 成本指标下 Y 轴刻度用 yuan() 渲染，出现 ¥（多个刻度）
    expect(screen.getAllByText((t) => t.startsWith("¥")).length).toBeGreaterThan(0);
  });

  it("空数据 → 空态", () => {
    useAnalyticsTimeseries.mockReturnValue({ data: { buckets: [] }, isLoading: false, isError: false, refetch: vi.fn() });
    render(<TrendChart range={range} />);
    expect(screen.getByText(copy.analytics.empty)).toBeInTheDocument();
  });
});
