import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
vi.mock("@/components/workbench/photo-image-form", () => ({
  PhotoImageForm: () => <div data-testid="photo-form" />
}));
vi.mock("@/components/workbench/copywriting-form", () => ({
  CopywritingForm: () => <div data-testid="copywriting-form" />
}));
vi.mock("@/components/workbench/ecom-image-cutout-form", () => ({
  EcomImageCutoutForm: () => <div data-testid="ecom-image-form" />
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

    // Switch to 图片生成 / 修改 → photo form (third mode).
    fireEvent.click(screen.getByRole("button", { name: /图片生成/ }));
    expect(screen.getByTestId("photo-form")).toBeInTheDocument();
    expect(screen.queryByTestId("avatar-form")).not.toBeInTheDocument();

    // Switch to 文案仿写 → copywriting form (fourth mode).
    fireEvent.click(screen.getByRole("button", { name: /文案仿写/ }));
    expect(screen.getByTestId("copywriting-form")).toBeInTheDocument();
    expect(screen.queryByTestId("photo-form")).not.toBeInTheDocument();

    // Switch to 电商图 → cutout form (fifth mode).
    fireEvent.click(screen.getByRole("button", { name: /电商图/ }));
    expect(screen.getByTestId("ecom-image-form")).toBeInTheDocument();
    expect(screen.queryByTestId("copywriting-form")).not.toBeInTheDocument();
  });
});
