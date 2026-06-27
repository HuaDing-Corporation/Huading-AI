import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 两个子工具占位为标记，本测试只断子工具切换 + 窄屏布局结构。
vi.mock("@/components/workbench/ecom-image-cutout-form", () => ({
  EcomImageCutoutForm: () => <div data-testid="cutout-form" />
}));
vi.mock("@/components/workbench/ecom-image-model-form", () => ({
  EcomImageModelForm: () => <div data-testid="model-form" />
}));

import { EcomImageWorkbench } from "./ecom-image-workbench";

describe("EcomImageWorkbench (电商图子工具切换)", () => {
  it("默认白底图，可切到 AI 模特并切回", () => {
    render(<EcomImageWorkbench />);
    expect(screen.getByTestId("cutout-form")).toBeInTheDocument();
    expect(screen.queryByTestId("model-form")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolModel }));
    expect(screen.getByTestId("model-form")).toBeInTheDocument();
    expect(screen.queryByTestId("cutout-form")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolCutout }));
    expect(screen.getByTestId("cutout-form")).toBeInTheDocument();
    expect(screen.queryByTestId("model-form")).not.toBeInTheDocument();
  });

  // 吸取 Phase1 chip 溢出教训：子工具切换用 grid-cols-2（半宽、窄屏不溢出），非单行 flex。
  it("子工具容器用 grid-cols-2（窄屏不溢出）", () => {
    render(<EcomImageWorkbench />);
    const group = screen.getByRole("group", { name: copy.workbench.ecomSubToolLabel });
    expect(group).toHaveClass("grid-cols-2");
  });
});
