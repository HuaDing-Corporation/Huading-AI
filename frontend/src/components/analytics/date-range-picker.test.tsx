import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DateRangePicker } from "./date-range-picker";
import { lastNDaysRange } from "@/lib/analytics/date";
import { copy } from "@/lib/copy";

describe("DateRangePicker", () => {
  it("点「近 7 天」预设 → onChange 近 7 天区间", () => {
    const onChange = vi.fn();
    render(<DateRangePicker range={lastNDaysRange(30)} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: copy.analytics.preset7 }));
    expect(onChange).toHaveBeenCalledWith(lastNDaysRange(7));
  });

  it("当前区间匹配预设 → 该预设 aria-pressed", () => {
    render(<DateRangePicker range={lastNDaysRange(30)} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: copy.analytics.preset30 })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: copy.analytics.preset7 })).toHaveAttribute("aria-pressed", "false");
  });

  it("改「起」日期 → onChange 带新 from", () => {
    const onChange = vi.fn();
    render(<DateRangePicker range={{ from: "2026-06-01", to: "2026-06-30" }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(copy.analytics.rangeFrom), { target: { value: "2026-06-10" } });
    expect(onChange).toHaveBeenCalledWith({ from: "2026-06-10", to: "2026-06-30" });
  });

  it("非法区间（from>to）→ 显式提示（承重·任务点名缺口）", () => {
    render(<DateRangePicker range={{ from: "2026-07-10", to: "2026-06-01" }} onChange={vi.fn()} />);
    expect(screen.getByText(copy.analytics.rangeInvalid)).toBeInTheDocument();
  });

  it("合法区间 → 不显示非法提示", () => {
    render(<DateRangePicker range={{ from: "2026-06-01", to: "2026-06-30" }} onChange={vi.fn()} />);
    expect(screen.queryByText(copy.analytics.rangeInvalid)).not.toBeInTheDocument();
  });
});
