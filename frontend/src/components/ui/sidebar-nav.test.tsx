import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarNav } from "./sidebar-nav";
import { navItems } from "@/lib/nav";
import { copy } from "@/lib/copy";

// 受控 pathname：各用例前置 nav.pathname 模拟“当前路由/直达”。null 用于验证 usePathname() ?? "" 兜底。
const nav = vi.hoisted(() => ({ pathname: "/" as string | null }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));
// next/link → 朴素 <a href>：href 由 navItems 透传，断接线（改错 href）即红。
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  )
}));

afterEach(() => {
  nav.pathname = "/";
});

describe("SidebarNav (侧边栏路由 · FIX3)", () => {
  it("已开通项渲染为链接，href 接线正确（承重·断接线即红）", () => {
    render(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "工作台" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "批量生产" })).toHaveAttribute("href", "/batch");
    expect(screen.getByRole("link", { name: "发布中心" })).toHaveAttribute("href", "/publish");
    // 数据看板转正（ANALYTICS-UI-0001）：SidebarNav 纯展示按 href 渲染为链接（仅管理员过滤由容器 Sidebar 负责）。
    expect(screen.getByRole("link", { name: "数据看板" })).toHaveAttribute("href", "/analytics");
  });

  it("直达 /batch → 批量生产高亮(aria-current=page)，工作台不高亮（承重·usePathname 推导）", () => {
    nav.pathname = "/batch";
    render(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "批量生产" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "工作台" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "发布中心" })).not.toHaveAttribute("aria-current");
  });

  it("工作台仅在根路径精确高亮（在 /batch 不误高亮）", () => {
    nav.pathname = "/";
    const { rerender } = render(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "工作台" })).toHaveAttribute("aria-current", "page");
    nav.pathname = "/batch";
    rerender(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "工作台" })).not.toHaveAttribute("aria-current");
  });

  it("子路径高亮：/batch/xxx 仍高亮批量生产", () => {
    nav.pathname = "/batch/abc123";
    render(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "批量生产" })).toHaveAttribute("aria-current", "page");
  });

  it("兄弟路由不误高亮：/batches（前缀但非子路径）不高亮批量生产（承重·+\"/\" 边界）", () => {
    nav.pathname = "/batches";
    render(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "批量生产" })).not.toHaveAttribute("aria-current");
  });

  it("pathname 为 null（usePathname 兜底）→ 不抛错且无任何项高亮", () => {
    nav.pathname = null;
    render(<SidebarNav items={navItems} />);
    for (const label of ["工作台", "批量生产", "发布中心"]) {
      expect(screen.getByRole("link", { name: label })).not.toHaveAttribute("aria-current");
    }
  });

  it("未开通项（模板中心/品牌库/封面工坊/团队）不是链接、无高亮、悬停「即将上线」（承重·点击不导航）", () => {
    nav.pathname = "/batch"; // 即便在已高亮的路由下，占位项也绝不高亮
    render(<SidebarNav items={navItems} />);
    for (const label of ["模板中心", "品牌库", "封面工坊", "团队"]) {
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument(); // 非链接 → 无路由目标
      const btn = screen.getByRole("button", { name: label });
      expect(btn).toHaveAttribute("title", copy.nav.comingSoon);
      expect(btn).not.toHaveAttribute("aria-current"); // 不误高亮
      expect(btn).not.toHaveAttribute("href");
    }
  });
});
