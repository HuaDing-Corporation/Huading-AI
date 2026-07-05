import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

import { ResolutionPicker } from "./resolution-picker";

describe("ResolutionPicker (共享三档 · ECOM-RESOLUTION-UI-0001)", () => {
  it("渲染 480P/720P/1080P 三档 + 「更高分辨率…」提示，value 档选中", () => {
    render(<ResolutionPicker value="720p" onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "480P" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "720P" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "1080P" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(copy.workbench.vgResolutionLabel)).toBeInTheDocument();
    expect(screen.getByText(copy.workbench.vgResolutionHint)).toBeInTheDocument();
  });

  it("受控 value 决定选中态（value=1080p → 1080P 选中、720P 不选）——自证受控契约，杀「忽略 value 硬编码 720p」变异", () => {
    render(<ResolutionPicker value="1080p" onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "1080P" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "720P" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "480P" })).toHaveAttribute("aria-pressed", "false");
  });

  it("点档位 → onChange 收到对应小写枚举值（480p/1080p）", () => {
    const onChange = vi.fn();
    render(<ResolutionPicker value="720p" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "1080P" }));
    expect(onChange).toHaveBeenLastCalledWith("1080p");
    fireEvent.click(screen.getByRole("button", { name: "480P" }));
    expect(onChange).toHaveBeenLastCalledWith("480p");
  });
});
