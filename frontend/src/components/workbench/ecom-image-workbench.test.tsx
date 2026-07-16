import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 三个子工具占位为标记，本测试只断子工具切换 + 惰性挂载/常驻 + prefill 时序 + 窄屏布局结构。
// ECOM-REPLICATE-UI-0001：营销海报入口移除 → 第三子工具改为「电商详情图」向导。
// ⚠️ 继续 mock 三个子工具（不要换成真实组件）：白底图与 AI 模特共用外壳 EcomImageTool，常驻后「生成方式」
//    group /「生成」按钮 / role=switch / 结果区 /「画面比例」在容器层会各出现两份 → 全局查询必撞。
//    真实子工具的承重覆盖见 ecom-image-workbench.keepalive.test.tsx（按 subtool-* scope 断言）。
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
  // ECOM-SUBTOOL-KEEPALIVE-UI-0001：切子工具不再卸载（旧行为 = state 全销毁 = 已传图/参数/已扣费整单全丢）。
  // 故不变量由「互斥**挂载**」改为「互斥**可见** + 惰性挂载」（与顶层 mode 同口径，见 (app)/page.test.tsx）。
  it("默认白底图；切子工具 → 目标可见、来过的仍挂载但不可见（惰性挂载 + 常驻保活）", () => {
    render(<EcomImageWorkbench />);
    expect(screen.getByTestId("cutout-form")).toBeVisible();
    // 惰性挂载：没访问过的子工具不在 DOM（AI 模特 mount 即发 GET /ecom-images/model-styles，不能白挂）。
    expect(screen.queryByTestId("model-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("detail-wizard")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolModel }));
    expect(screen.getByTestId("model-form")).toBeVisible();
    // 常驻：白底图仍在 DOM（输入保活）但已隐藏。
    expect(screen.getByTestId("cutout-form")).toBeInTheDocument();
    expect(screen.getByTestId("cutout-form")).not.toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolDetail }));
    expect(screen.getByTestId("detail-wizard")).toBeVisible();
    expect(screen.getByTestId("model-form")).not.toBeVisible();

    // 营销海报入口已彻底移除 → 无该子工具按钮
    expect(screen.queryByRole("button", { name: copy.workbench.ecomSubToolPoster })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolCutout }));
    expect(screen.getByTestId("cutout-form")).toBeVisible();
    expect(screen.getByTestId("detail-wizard")).not.toBeVisible();
    // 三个都还挂着，只有一个可见。
    expect(screen.getByTestId("model-form")).not.toBeVisible();
  });

  // a11y 硬门（同 (app)/page.test.tsx）：隐藏子工具必须真从可及性树消失 —— 用 HTML hidden 属性，
  // 且隐藏态不得挂任何设 display 的 class（否则 CSS 盖掉 [hidden] 的 UA 样式 → 读屏读到 3 份表单、焦点掉进去）。
  it("a11y：隐藏子工具带 hidden 属性且无 display class 覆盖；激活的用 display:contents（布局不变）", () => {
    render(<EcomImageWorkbench />);
    fireEvent.click(screen.getByRole("button", { name: copy.workbench.ecomSubToolModel }));

    const hidden = screen.getByTestId("subtool-cutout");
    const active = screen.getByTestId("subtool-model");
    expect(hidden).toHaveAttribute("hidden");
    expect(hidden).not.toBeVisible();
    expect(hidden.className).toBe("");
    expect(active).not.toHaveAttribute("hidden");
    expect(active).toHaveClass("contents");
  });

  // 🔴 承重：反推「带入 AI 模特」时 prefill 是**后到**的（电商图面板常驻 → 本容器不重挂）。
  // 若 effect 只 setTool 而不把该子工具加进 mounted 列表 → 子工具永不挂载 → 永不消费/上报 →
  // clearPrefill 永不调用 → 父级缓冲永久滞留，且面板空白（本 effect 的 deps 此后不变，无法自愈）。
  it("后到的 prefill：initialTool 由 undefined 变 model → 该子工具入列挂载并可见（钉死 activate 而非裸 setTool）", () => {
    const { rerender } = render(<EcomImageWorkbench />);
    expect(screen.getByTestId("cutout-form")).toBeVisible();
    expect(screen.queryByTestId("model-form")).not.toBeInTheDocument();

    rerender(<EcomImageWorkbench initialTool="model" initialCustom="工作室柔光" onPrefillConsumed={vi.fn()} />);

    expect(screen.getByTestId("model-form")).toBeVisible();
    expect(screen.getByTestId("cutout-form")).not.toBeVisible();
  });

  // prefill 首挂路径：面板因带入而首次挂载时，目标子工具必须在同一 commit 内就位（否则接不住 initialCustom）。
  it("首挂即带 prefill：initialTool=model → 直接挂 AI 模特，且白底图不被白挂", () => {
    render(<EcomImageWorkbench initialTool="model" initialCustom="工作室柔光" onPrefillConsumed={vi.fn()} />);
    expect(screen.getByTestId("model-form")).toBeVisible();
    expect(screen.queryByTestId("cutout-form")).not.toBeInTheDocument();
  });

  // WORKBENCH-KEEPALIVE-UI-0001 定下的父子时序（本包改了该 effect，须确保没破坏）：
  // 带 custom 时父**故意不抢报**，让子组件消费后自己上报；否则 effect「子先父后」会让 initialCustom
  // 在子挂载前回落 undefined → 值丢失。不带 custom 时才由父直接结清缓冲。
  it("prefill 带 custom → 父不抢报（让位给子组件上报）", () => {
    const onConsumed = vi.fn();
    render(<EcomImageWorkbench initialTool="model" initialCustom="工作室柔光" onPrefillConsumed={onConsumed} />);
    expect(onConsumed).not.toHaveBeenCalled();
  });

  it("prefill 不带 custom → 父直接结清缓冲（无人代劳）", () => {
    const onConsumed = vi.fn();
    render(<EcomImageWorkbench initialTool="model" onPrefillConsumed={onConsumed} />);
    expect(onConsumed).toHaveBeenCalledTimes(1);
  });

  // 吸取 Phase1/2 溢出教训：3 子工具用 grid-cols-3（等宽、窄屏不溢出），非单行 flex。
  it("子工具容器用 grid-cols-3（3 项窄屏不溢出）", () => {
    render(<EcomImageWorkbench />);
    const group = screen.getByRole("group", { name: copy.workbench.ecomSubToolLabel });
    expect(group).toHaveClass("grid-cols-3");
  });
});
