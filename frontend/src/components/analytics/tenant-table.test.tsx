import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

const useAnalyticsByTenant = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/hooks", () => ({ useAnalyticsByTenant }));

import { TenantTable } from "./tenant-table";

const range = { from: "2026-06-01", to: "2026-06-30" };
const item = {
  tenant_id: "ten-1",
  tenant_name: "租户 1",
  credits_used: 1200,
  cost_cents: 45600,
  task_count: 88,
  success_rate: 0.965,
  balance: { total: 2000, used: 800, reserved: 150, remaining: 1050 }
};
const lastOpts = () => useAnalyticsByTenant.mock.calls.at(-1)![1] as { sort: string; limit: number; offset: number };

beforeEach(() => {
  useAnalyticsByTenant.mockReset();
  useAnalyticsByTenant.mockReturnValue({ data: { items: [item], total: 46 }, isLoading: false, isError: false, refetch: vi.fn() });
});

describe("TenantTable (排序 + 分页 承重)", () => {
  it("初始：credits_desc / limit 20 / offset 0；成功率 ×100% 展示", () => {
    render(<TenantTable range={range} />);
    expect(lastOpts()).toMatchObject({ sort: "credits_desc", limit: 20, offset: 0 });
    expect(screen.getByText("96.5%")).toBeInTheDocument(); // success_rate 0.965 → 96.5%
    expect(screen.getByText("¥456.00")).toBeInTheDocument(); // cost_cents/100
  });

  it("点「成本」表头 → cost_desc，再点 → cost_asc（承重·8 种排序映射）", () => {
    render(<TenantTable range={range} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colCost }));
    expect(lastOpts().sort).toBe("cost_desc");
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colCost }));
    expect(lastOpts().sort).toBe("cost_asc");
  });

  it("点「任务量」表头 → task_count_desc；再点 → task_count_asc（承重·字段映射 + asc）", () => {
    render(<TenantTable range={range} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colTasks }));
    expect(lastOpts().sort).toBe("task_count_desc");
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colTasks }));
    expect(lastOpts().sort).toBe("task_count_asc");
  });

  it("点「成功率」表头 → success_rate_desc；再点 → success_rate_asc（承重·补齐 8 枚举，杀字段名变异）", () => {
    render(<TenantTable range={range} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colSuccessRate }));
    expect(lastOpts().sort).toBe("success_rate_desc");
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colSuccessRate }));
    expect(lastOpts().sort).toBe("success_rate_asc");
  });

  it("点默认列「消耗积分」→ credits_asc（初始 credits_desc → toggle）", () => {
    render(<TenantTable range={range} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.colCredits }));
    expect(lastOpts().sort).toBe("credits_asc");
  });

  it("下一页 → offset 20；上一页初始禁用（承重·分页）", () => {
    render(<TenantTable range={range} />);
    expect(screen.getByRole("button", { name: copy.analytics.prevPage })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.nextPage }));
    expect(lastOpts().offset).toBe(20);
  });

  it("余额四值分列：总/已用/预留/剩余 都在（reserved 与已用分开）", () => {
    render(<TenantTable range={range} />);
    expect(screen.getByText(`${copy.analytics.balReserved} 150`)).toBeInTheDocument();
    expect(screen.getByText(`${copy.analytics.balUsed} 800`)).toBeInTheDocument();
  });

  it("末页：offset 达 total 边界 → 下一页禁用 + pageRange 钳制（承重·分页边界）", () => {
    // total 46, limit 20 → 末页 offset 40，rangeTo=min(60,46)=46。
    useAnalyticsByTenant.mockReturnValue({ data: { items: [item], total: 46 }, isLoading: false, isError: false, refetch: vi.fn() });
    render(<TenantTable range={range} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.nextPage })); // offset 20
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.nextPage })); // offset 40
    expect(lastOpts().offset).toBe(40);
    expect(screen.getByRole("button", { name: copy.analytics.nextPage })).toBeDisabled();
    expect(screen.getByText(copy.analytics.pageRange(41, 46, 46))).toBeInTheDocument();
  });

  it("total=0 → 空态（不渲染分页）", () => {
    useAnalyticsByTenant.mockReturnValue({ data: { items: [], total: 0 }, isLoading: false, isError: false, refetch: vi.fn() });
    render(<TenantTable range={range} />);
    expect(screen.getByText(copy.analytics.empty)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: copy.analytics.nextPage })).not.toBeInTheDocument();
  });
});
