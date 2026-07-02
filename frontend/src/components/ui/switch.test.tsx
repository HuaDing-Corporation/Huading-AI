import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Switch } from "./switch";

describe("Switch (设计系统开关)", () => {
  it("role=switch + aria-checked 反映 checked", () => {
    const { rerender } = render(<Switch checked={false} onCheckedChange={vi.fn()} ariaLabel="开关" />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    rerender(<Switch checked onCheckedChange={vi.fn()} ariaLabel="开关" />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("点击 → onCheckedChange(取反)", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onCheckedChange={onChange} ariaLabel="开关" />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("disabled → 不触发", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onCheckedChange={onChange} ariaLabel="开关" disabled />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
