import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DurationPicker, isValidDuration } from "./duration-picker";

afterEach(() => vi.clearAllMocks());

describe("DurationPicker (电商带货 视频时长)", () => {
  it("renders 10/15/30 gears + 自定义（ECOM-FIXES-0001：去掉 45/60），reflects the selected preset", () => {
    render(<DurationPicker value={30} onChange={vi.fn()} />);
    for (const s of [10, 15, 30]) {
      expect(screen.getByRole("button", { name: `${s} 秒` })).toBeInTheDocument();
    }
    // 承重：45/60 档已移除，不得再出现。
    expect(screen.queryByRole("button", { name: "45 秒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "60 秒" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "30 秒" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "自定义" })).toBeInTheDocument();
    // No custom input until 自定义 is chosen.
    expect(screen.queryByLabelText("自定义时长（秒）")).not.toBeInTheDocument();
  });

  it("selecting a gear calls onChange with that duration", () => {
    const onChange = vi.fn();
    render(<DurationPicker value={10} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "30 秒" }));
    expect(onChange).toHaveBeenCalledWith(30);
  });

  it("custom mode shows a number input, propagates the value, and validates 5–120", () => {
    const onChange = vi.fn();
    render(<DurationPicker value={30} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));

    const input = screen.getByLabelText("自定义时长（秒）");
    fireEvent.change(input, { target: { value: "90" } });
    expect(onChange).toHaveBeenLastCalledWith(90);
    expect(screen.queryByText("请输入 5–120 秒")).not.toBeInTheDocument();

    // Out of range → error surfaces.
    fireEvent.change(input, { target: { value: "200" } });
    expect(onChange).toHaveBeenLastCalledWith(200);
    expect(screen.getByText("请输入 5–120 秒")).toBeInTheDocument();
  });

  it("isValidDuration enforces the 5–120 inclusive range", () => {
    expect(isValidDuration(5)).toBe(true);
    expect(isValidDuration(120)).toBe(true);
    expect(isValidDuration(30)).toBe(true);
    expect(isValidDuration(4)).toBe(false);
    expect(isValidDuration(121)).toBe(false);
    expect(isValidDuration(Number.NaN)).toBe(false);
  });
});
