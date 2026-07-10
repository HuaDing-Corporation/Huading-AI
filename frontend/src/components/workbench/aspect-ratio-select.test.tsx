import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { AspectRatioSelect, DEFAULT_IMAGE_ASPECT_RATIO, IMAGE_ASPECT_RATIOS } from "./aspect-ratio-select";

// IMAGE-ASPECT-RATIO-UI-0001 · 画面比例选择器：9 档（8 定比 + 自适应），默认 1:1，自适应展开语义 hint。
describe("AspectRatioSelect (画面比例)", () => {
  it("默认常量为 1:1；对齐 BE 9 档枚举（含 auto）", () => {
    expect(DEFAULT_IMAGE_ASPECT_RATIO).toBe("1:1");
    expect(IMAGE_ASPECT_RATIOS).toEqual(["1:1", "4:3", "3:2", "16:9", "21:9", "3:4", "2:3", "9:16", "auto"]);
  });

  it("展示标签 + 当前值；打开后 9 档齐备（21:9 与 自适应 均在）", () => {
    render(<AspectRatioSelect value="1:1" onValueChange={vi.fn()} />);
    expect(screen.getByText(copy.workbench.aspectLabel)).toBeInTheDocument();
    // combobox 程序化关联 label（可及名含「画面比例」，读屏可听上下文）。
    expect(screen.getByRole("combobox", { name: new RegExp(copy.workbench.aspectLabel) })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("combobox"));
    expect(screen.getAllByRole("option")).toHaveLength(9);
    expect(screen.getByRole("option", { name: /21:9/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: copy.workbench.aspectAuto })).toBeInTheDocument();
  });

  it("选一档 → onValueChange 回传该比例值", () => {
    const onChange = vi.fn();
    render(<AspectRatioSelect value="1:1" onValueChange={onChange} />);
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(screen.getByRole("option", { name: /9:16/ }));
    expect(onChange).toHaveBeenCalledWith("9:16");
  });

  it("自适应：选中 auto 展开语义 hint（有输入图按图、无输入图 1:1）；非 auto 不显示", () => {
    const { rerender } = render(<AspectRatioSelect value="1:1" onValueChange={vi.fn()} />);
    expect(screen.queryByText(copy.workbench.aspectAutoHint)).not.toBeInTheDocument();
    rerender(<AspectRatioSelect value="auto" onValueChange={vi.fn()} />);
    expect(screen.getByText(copy.workbench.aspectAutoHint)).toBeInTheDocument();
  });
});
