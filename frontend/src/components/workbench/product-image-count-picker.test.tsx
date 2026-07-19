import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  IMAGE_COUNT_MAX,
  IMAGE_COUNT_MIN,
  isValidImageCount,
  ProductImageCountPicker
} from "./product-image-count-picker";

describe("ProductImageCountPicker (产品图张数)", () => {
  it("isValidImageCount：整数 1–9 合法，越界/非整/NaN 非法（张数门禁的真值来源）", () => {
    expect(isValidImageCount(IMAGE_COUNT_MIN)).toBe(true);
    expect(isValidImageCount(IMAGE_COUNT_MAX)).toBe(true);
    expect(isValidImageCount(3)).toBe(true);
    expect(isValidImageCount(0)).toBe(false); // 保底至少 1
    expect(isValidImageCount(10)).toBe(false); // 上限 9
    expect(isValidImageCount(2.5)).toBe(false); // 非整
    expect(isValidImageCount(Number.NaN)).toBe(false); // 空/非数字自定义输入
  });

  it("预设 1–5 + 自定义档；点档位上抛对应值（aria-pressed 反映选中）", () => {
    const onChange = vi.fn();
    render(<ProductImageCountPicker value={1} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "1 张" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "3 张" }));
    expect(onChange).toHaveBeenLastCalledWith(3);
  });

  it("自定义档：合法值上抛数字、越界显错（1–9 范围承重）", () => {
    const onChange = vi.fn();
    render(<ProductImageCountPicker value={2} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    const input = screen.getByLabelText("自定义张数");

    // 合法 7 → 上抛 7，无错。
    fireEvent.change(input, { target: { value: "7" } });
    expect(onChange).toHaveBeenLastCalledWith(7);
    expect(screen.queryByText("请输入 1–9 张")).not.toBeInTheDocument();

    // 越界 12 → 显错（父级据 isValidImageCount 卡提交）。
    fireEvent.change(input, { target: { value: "12" } });
    expect(screen.getByText("请输入 1–9 张")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });
});
