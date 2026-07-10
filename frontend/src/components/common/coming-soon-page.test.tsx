import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// 隔离外壳（Sidebar/TopBar 各有专测），只锁占位页内容 + 返回。
const router = vi.hoisted(() => ({ push: vi.fn(), back: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <aside data-testid="sidebar" /> }));
vi.mock("@/components/layout/top-bar", () => ({ TopBar: () => <header data-testid="topbar" /> }));

import { ComingSoonPage } from "./coming-soon-page";

describe("ComingSoonPage (统一即将上线占位)", () => {
  it("标题带板块名 + 「（即将上线）」后缀 + 友好占位文案", () => {
    render(<ComingSoonPage title="模板中心" />);
    expect(screen.getByRole("heading", { name: `模板中心${copy.comingSoon.navSuffix}` })).toBeInTheDocument();
    expect(screen.getByText(copy.comingSoon.title)).toBeInTheDocument();
    expect(screen.getByText(copy.comingSoon.desc)).toBeInTheDocument();
  });

  it("「返回工作台」→ router.push('/')", () => {
    render(<ComingSoonPage title="团队" />);
    fireEvent.click(screen.getByRole("button", { name: copy.comingSoon.back }));
    expect(router.push).toHaveBeenCalledWith("/");
  });
});
