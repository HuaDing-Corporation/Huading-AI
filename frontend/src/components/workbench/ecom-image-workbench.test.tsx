import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 三个子工具占位为标记，本测试只断子工具切换 + 窄屏布局结构。
// ECOM-REPLICATE-UI-0001：营销海报入口移除 → 第三子工具改为「电商详情图」向导。
vi.mock("@/components/workbench/ecom-image-cutout-form", () => ({
  EcomImageCutoutForm: () => <div data-testid="cutout-form" />
}));
vi.mock("@/components/workbench/ecom-image-model-form", () => ({
  EcomImageModelForm: () => <div data-testid="model-form" />
}));
vi.mock("@/components/workbench/ecom-detail-wizard", () => ({
  EcomDetailWizard: () => <div data-testid="detail-wizard" />
}));

import { EcomImageWorkbench } from "./ecom-image-workbench";

describe("EcomImageWorkbench (电商图子工具切换 · 3 项)", () => {
  it("默认白底图，可切到 AI 模特 / 电商详情图并切回（营销海报已下线）", () => {
    render(<EcomImageWorkbench />);
    expect(screen.getByTestId("cutout-form")).toBeInTheDocument();
    expect(screen.queryByTestId("model-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("detail-wizard")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolModel }));
    expect(screen.getByTestId("model-form")).toBeInTheDocument();
    expect(screen.queryByTestId("cutout-form")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolDetail }));
    expect(screen.getByTestId("detail-wizard")).toBeInTheDocument();
    expect(screen.queryByTestId("model-form")).not.toBeInTheDocument();

    // 营销海报入口已彻底移除 → 无该子工具按钮
    expect(screen.queryByRole("button", { name: copy.workbench.ecomSubToolPoster })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolCutout }));
    expect(screen.getByTestId("cutout-form")).toBeInTheDocument();
    expect(screen.queryByTestId("detail-wizard")).not.toBeInTheDocument();
  });

  // 吸取 Phase1/2 溢出教训：3 子工具用 grid-cols-3（等宽、窄屏不溢出），非单行 flex。
  it("子工具容器用 grid-cols-3（3 项窄屏不溢出）", () => {
    render(<EcomImageWorkbench />);
    const group = screen.getByRole("group", { name: copy.workbench.ecomSubToolLabel });
    expect(group).toHaveClass("grid-cols-3");
  });
});
