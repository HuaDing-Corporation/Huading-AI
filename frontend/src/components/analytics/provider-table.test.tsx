import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 坑①：by-provider 计数列语义是「计费笔数」，绝不标「任务量」。
const useAnalyticsByProvider = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/hooks", () => ({ useAnalyticsByProvider }));

import { ProviderTable } from "./provider-table";

const range = { from: "2026-06-01", to: "2026-06-30" };

describe("ProviderTable (坑①·计费笔数)", () => {
  it("计数列名是「计费笔数」，页面不出现「任务量」（承重·防运营误读）", () => {
    useAnalyticsByProvider.mockReturnValue({
      data: { items: [{ provider: "copywriting", model: null, credits_used: 6800, cost_cents: 210400, task_count: 980, share_pct: 14.1 }] },
      isLoading: false,
      isError: false
    });
    render(<ProviderTable range={range} />);
    expect(screen.getByText(copy.analytics.colBillingCount)).toBeInTheDocument(); // 「计费笔数」
    expect(screen.getByText(copy.analytics.colBillingCount)).toHaveTextContent("计费笔数");
    expect(screen.queryByText("任务量")).not.toBeInTheDocument(); // 绝不叫任务量
    expect(screen.getByText("¥2,104.00")).toBeInTheDocument(); // cost_cents/100 显示 ¥
    expect(screen.getByText("980")).toBeInTheDocument(); // 计费笔数值
  });

  it("空数据 → 空态提示", () => {
    useAnalyticsByProvider.mockReturnValue({ data: { items: [] }, isLoading: false, isError: false });
    render(<ProviderTable range={range} />);
    expect(screen.getByText(copy.analytics.empty)).toBeInTheDocument();
  });
});
