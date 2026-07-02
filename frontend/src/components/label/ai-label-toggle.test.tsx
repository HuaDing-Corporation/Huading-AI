import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { AiLabelToggle } from "./ai-label-toggle";

describe("AiLabelToggle (AI 标识开关 + 提示)", () => {
  it("渲染标签 + 平台声明提示 + 受控 switch", () => {
    render(<AiLabelToggle checked={false} onChange={vi.fn()} />);
    expect(screen.getByText(copy.label.toggleLabel)).toBeInTheDocument();
    expect(screen.getByText(copy.label.toggleHint)).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("checked=true → aria-checked true", () => {
    render(<AiLabelToggle checked onChange={vi.fn()} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("切换 → onChange(取反)", () => {
    const onChange = vi.fn();
    render(<AiLabelToggle checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
