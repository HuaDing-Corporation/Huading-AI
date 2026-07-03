import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OverviewCards } from "./overview-cards";
import { copy } from "@/lib/copy";
import type { AnalyticsOverview } from "@/lib/api/types";

const data: AnalyticsOverview = {
  total_credits_used: 48213.5,
  total_cost_cents: 1892340,
  task_count: 5230,
  success_count: 4890,
  failed_count: 340,
  tenant_count: 46,
  period: { from: "2026-06-01", to: "2026-06-30" }
};

describe("OverviewCards", () => {
  it("加载态 → 骨架，不渲染数字", () => {
    render(<OverviewCards data={undefined} isLoading isError={false} reservedSum={undefined} reservedCount={undefined} reservedComplete={undefined} />);
    expect(screen.queryByText(copy.analytics.ovCredits)).not.toBeInTheDocument();
  });

  it("错误态 → 错误提示", () => {
    render(<OverviewCards data={undefined} isLoading={false} isError reservedSum={undefined} reservedCount={undefined} reservedComplete={undefined} />);
    expect(screen.getByText(copy.analytics.error)).toBeInTheDocument();
  });

  it("数据态：积分/成本¥/任务量/成功·失败/租户数/预留 全渲染，成本按 cost_cents/100", () => {
    render(<OverviewCards data={data} isLoading={false} isError={false} reservedSum={6722} reservedCount={46} reservedComplete={true} />);
    expect(screen.getByText("48,213.5")).toBeInTheDocument(); // 积分
    expect(screen.getByText("¥18,923.40")).toBeInTheDocument(); // 1892340/100
    expect(screen.getByText("5,230")).toBeInTheDocument(); // 任务量
    expect(screen.getByText("4,890")).toBeInTheDocument(); // 成功
    expect(screen.getByText("340")).toBeInTheDocument(); // 失败
    expect(screen.getByText("46")).toBeInTheDocument(); // 租户数
    expect(screen.getByText("6,722")).toBeInTheDocument(); // 预留求和
  });

  it("预留口径：complete → 「全 N 租户合计」；非 complete → 「消耗榜前 N」（承重·口径诚实）", () => {
    const { rerender } = render(
      <OverviewCards data={data} isLoading={false} isError={false} reservedSum={6722} reservedCount={46} reservedComplete={true} />
    );
    expect(screen.getByText(copy.analytics.ovReservedHintAll(46))).toBeInTheDocument();
    rerender(<OverviewCards data={data} isLoading={false} isError={false} reservedSum={9000} reservedCount={100} reservedComplete={false} />);
    expect(screen.getByText(copy.analytics.ovReservedHintTop(100))).toBeInTheDocument();
  });
});
