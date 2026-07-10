import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// Mock the heavy children to markers — this test only asserts the mode switch.
vi.mock("next/navigation", () => ({ useRouter: () => ({ back: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/layout/top-bar", () => ({ TopBar: () => <div data-testid="topbar" /> }));
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
  it("defaults to the avatar form and toggles to the i2v form and back", () => {
    render(<Home />);

    // Default = 数字人口播 → avatar form; task list shared/always present.
    expect(screen.getByTestId("avatar-form")).toBeInTheDocument();
    expect(screen.queryByTestId("ecom-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("tasklist")).toBeInTheDocument();

    // Switch to 电商带货 → i2v form replaces the avatar form.
    fireEvent.click(screen.getByRole("button", { name: /电商带货/ }));
    expect(screen.getByTestId("ecom-form")).toBeInTheDocument();
    expect(screen.queryByTestId("avatar-form")).not.toBeInTheDocument();

    // Switch back → original 口播 form intact (不破现有数字人口播表单).
    fireEvent.click(screen.getByRole("button", { name: /数字人口播/ }));
    expect(screen.getByTestId("avatar-form")).toBeInTheDocument();
    expect(screen.queryByTestId("ecom-form")).not.toBeInTheDocument();

    // Switch to 图片生成 / 修改 → photo form (WORKBENCH-TAB-ORDER-0001: 3rd tab). 名称匹配，与顺序无关。
    fireEvent.click(screen.getByRole("button", { name: /图片生成/ }));
    expect(screen.getByTestId("photo-form")).toBeInTheDocument();
    expect(screen.queryByTestId("avatar-form")).not.toBeInTheDocument();

    // Switch to 文案仿写 → copywriting form (5th tab).
    fireEvent.click(screen.getByRole("button", { name: /文案仿写/ }));
    expect(screen.getByTestId("copywriting-form")).toBeInTheDocument();
    expect(screen.queryByTestId("photo-form")).not.toBeInTheDocument();

    // Switch to 电商图 → cutout form (4th tab).
    fireEvent.click(screen.getByRole("button", { name: /电商图/ }));
    expect(screen.getByTestId("ecom-image-form")).toBeInTheDocument();
    expect(screen.queryByTestId("copywriting-form")).not.toBeInTheDocument();

    // Switch to 视频生成 → video-gen form (7th tab, VIDEOGEN-UI-0001).
    fireEvent.click(screen.getByRole("button", { name: /视频生成/ }));
    expect(screen.getByTestId("video-gen-form")).toBeInTheDocument();
    expect(screen.queryByTestId("ecom-image-form")).not.toBeInTheDocument();
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
