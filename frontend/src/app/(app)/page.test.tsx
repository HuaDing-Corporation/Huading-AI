import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// Mock the heavy children to markers — this test only asserts the mode switch.
vi.mock("next/navigation", () => ({ useRouter: () => ({ back: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/layout/top-bar", () => ({ TopBar: () => <div data-testid="topbar" /> }));
// LANDING-CONTACT-UI-0001：横幅有专门测试（contact-flow.test.tsx），page 级与 TopBar 同款 mock 掉。
vi.mock("@/components/contact/welcome-contact-banner", () => ({ WelcomeContactBanner: () => null }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
vi.mock("@/components/tasks/task-list", () => ({ TaskList: () => <div data-testid="tasklist" /> }));
vi.mock("@/components/workbench/new-video-form", () => ({
  NewVideoForm: () => <div data-testid="avatar-form" />
}));
vi.mock("@/components/workbench/ecom-video-form", () => ({
  EcomVideoForm: () => <div data-testid="ecom-form" />
}));
vi.mock("@/components/workbench/video-gen-form", () => ({
  VideoGenForm: () => <div data-testid="video-gen-form" />
}));
vi.mock("@/components/workbench/photo-image-form", () => ({
  PhotoImageForm: () => <div data-testid="photo-form" />
}));
vi.mock("@/components/workbench/copywriting-form", () => ({
  CopywritingForm: () => <div data-testid="copywriting-form" />
}));
vi.mock("@/components/workbench/ecom-image-workbench", () => ({
  EcomImageWorkbench: () => <div data-testid="ecom-image-form" />
}));
vi.mock("@/components/tasks/generation-history", () => ({
  GenerationHistory: () => <div data-testid="history" />
}));

import Home from "./page";

describe("Workbench mode switch (数字人口播 / 电商带货)", () => {
  // WORKBENCH-KEEPALIVE-UI-0001：切 mode 不再卸载表单（旧行为 = React state 全销毁 = 用户输入全丢）。
  // 故这里的不变量由「互斥**挂载**」改为「互斥**可见** + 惰性挂载」：没访问过的不在 DOM；访问过的留在 DOM 但 hidden。
  it("默认口播；切 mode → 目标可见、来过的表单仍挂载但不可见（惰性挂载 + 常驻保活）", () => {
    render(<Home />);

    // Default = 数字人口播 → avatar form; task list shared/always present.
    expect(screen.getByTestId("avatar-form")).toBeVisible();
    // 惰性挂载：没访问过的表单根本不在 DOM（不把它们 mount 时的 GET 打到首屏）。
    expect(screen.queryByTestId("ecom-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("tasklist")).toBeInTheDocument();

    // 切到电商带货 → 目标可见；口播表单仍在 DOM（输入保活），但 hidden → 不可见、读屏/Tab 不可达。
    fireEvent.click(screen.getByRole("button", { name: /电商带货/ }));
    expect(screen.getByTestId("ecom-form")).toBeVisible();
    expect(screen.getByTestId("avatar-form")).toBeInTheDocument();
    expect(screen.getByTestId("avatar-form")).not.toBeVisible();

    // 切回 → 口播可见、电商转隐藏；两者都还挂着（不破现有数字人口播表单）。
    fireEvent.click(screen.getByRole("button", { name: /数字人口播/ }));
    expect(screen.getByTestId("avatar-form")).toBeVisible();
    expect(screen.getByTestId("ecom-form")).not.toBeVisible();

    // 图片生成 / 修改（WORKBENCH-TAB-ORDER-0001: 3rd tab）。名称匹配，与顺序无关。
    fireEvent.click(screen.getByRole("button", { name: /图片生成/ }));
    expect(screen.getByTestId("photo-form")).toBeVisible();
    expect(screen.getByTestId("avatar-form")).not.toBeVisible();

    // 文案仿写（5th tab）。
    fireEvent.click(screen.getByRole("button", { name: /文案仿写/ }));
    expect(screen.getByTestId("copywriting-form")).toBeVisible();
    expect(screen.getByTestId("photo-form")).not.toBeVisible();

    // 电商图（4th tab）。
    fireEvent.click(screen.getByRole("button", { name: /电商图/ }));
    expect(screen.getByTestId("ecom-image-form")).toBeVisible();
    expect(screen.getByTestId("copywriting-form")).not.toBeVisible();

    // 视频生成（7th tab, VIDEOGEN-UI-0001）。
    fireEvent.click(screen.getByRole("button", { name: /视频生成/ }));
    expect(screen.getByTestId("video-gen-form")).toBeVisible();
    expect(screen.getByTestId("ecom-image-form")).not.toBeVisible();

    // 六个来过的面板全在 DOM，但只有一个可见。
    for (const id of ["avatar-form", "ecom-form", "photo-form", "copywriting-form", "ecom-image-form"]) {
      expect(screen.getByTestId(id)).not.toBeVisible();
    }
  });

  // WORKBENCH-KEEPALIVE-UI-0001 · a11y 硬门：隐藏面板必须真从可及性树消失。用 HTML `hidden` 属性
  // （UA 样式 [hidden]{display:none}）—— 只加 Tailwind class 会被 CSS 覆盖且 jsdom 测不出；只用 aria-hidden
  // 更糟（元素仍可被 Tab 聚焦 → 看不见的焦点黑洞）。隐藏态不得挂任何设 display 的 class。
  it("a11y：隐藏面板带 hidden 属性且无 display class 覆盖；激活面板 display:contents（布局不变）", () => {
    render(<Home />);
    fireEvent.click(screen.getByRole("button", { name: /电商带货/ }));

    const hiddenPanel = screen.getByTestId("panel-avatar_talk");
    const activePanel = screen.getByTestId("panel-seedance_i2v");

    expect(hiddenPanel).toHaveAttribute("hidden");
    expect(hiddenPanel).not.toBeVisible();
    // 关键：隐藏态 className 为空——任何 display 类（flex/grid/contents/block）都会盖掉 [hidden] 的 UA 样式。
    expect(hiddenPanel.className).toBe("");
    // 激活态：display:contents → wrapper 透明，表单本体仍是 grid 直接子项 → 布局与改造前逐像素一致。
    expect(activePanel).not.toHaveAttribute("hidden");
    expect(activePanel).toHaveClass("contents");
  });

  // WORKBENCH-TAB-ORDER-0001：锁死 7 tab 顺序（用户 2026-07-10 指定），防未来误重排。
  it("7 tab 顺序：数字人口播·提示词反推·图片生成/修改·电商图·文案仿写·电商带货·视频生成", () => {
    render(<Home />);
    const group = screen.getByRole("group", { name: "生成模式" });
    const labels = within(group)
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(labels).toEqual([
      copy.workbench.modeAvatar,
      copy.workbench.modeReverse,
      copy.workbench.modePhoto,
      copy.workbench.modeEcomImage,
      copy.workbench.modeCopywriting,
      copy.workbench.modeEcom,
      copy.workbench.modeVideoGen
    ]);
  });

  // FIX1 P1：5 模式 chip 容器需有窄屏溢出保护（横向滚动 + chip 不压缩），
  // 否则 ~375/390px 撑破页面。jsdom 无布局，断结构性保护类。
  it("模式 chip 容器有窄屏溢出保护（overflow-x-auto + chip shrink-0）", () => {
    render(<Home />);
    const group = screen.getByRole("group", { name: "生成模式" });
    expect(group).toHaveClass("overflow-x-auto");
    // 每个 chip 不被压缩、文字不换行，滚动时保持原尺寸。
    const chip = screen.getByRole("button", { name: /电商图/ });
    expect(chip).toHaveClass("shrink-0");
    expect(chip).toHaveClass("whitespace-nowrap");
  });
});
