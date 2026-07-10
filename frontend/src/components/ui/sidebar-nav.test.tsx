import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarNav } from "./sidebar-nav";
import { navItems } from "@/lib/nav";

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
    // 发布中心走 coming-soon gate（UI-COMINGSOON-TENANT-RENAME-0001）：名称带「（即将上线）」，仍为可点 Link → /publish。
    expect(screen.getByRole("link", { name: "发布中心（即将上线）" })).toHaveAttribute("href", "/publish");
    // 数据看板转正（ANALYTICS-UI-0001）：SidebarNav 纯展示按 href 渲染为链接（仅管理员过滤由容器 Sidebar 负责）。
    expect(screen.getByRole("link", { name: "数据看板" })).toHaveAttribute("href", "/analytics");
  });

  it("直达 /batch → 批量生产高亮(aria-current=page)，工作台不高亮（承重·usePathname 推导）", () => {
    nav.pathname = "/batch";
    render(<SidebarNav items={navItems} />);
    expect(screen.getByRole("link", { name: "批量生产" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "工作台" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "发布中心（即将上线）" })).not.toHaveAttribute("aria-current");
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
    for (const label of ["工作台", "批量生产", "发布中心（即将上线）"]) {
      expect(screen.getByRole("link", { name: label })).not.toHaveAttribute("aria-current");
    }
  });

  // coming-soon 板块（含封面工坊 UI-COMINGSOON-COVER-0001）：占位升为可点 Link（带后缀），不误高亮。
  it("coming-soon 板块（模板中心/品牌库/封面工坊/团队）为可点 Link + 「（即将上线）」后缀，不误高亮", () => {
    nav.pathname = "/batch"; // 即便在已高亮的路由下，coming-soon 项也不误高亮
    render(<SidebarNav items={navItems} />);
    const soon: [string, string][] = [
      ["模板中心（即将上线）", "/templates"],
      ["品牌库（即将上线）", "/brand-library"],
      ["封面工坊（即将上线）", "/covers"],
      ["团队（即将上线）", "/team"]
    ];
    for (const [label, href] of soon) {
      const link = screen.getByRole("link", { name: label });
      expect(link).toHaveAttribute("href", href);
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("封面工坊：纳入 coming-soon gate 后为可点 Link（不再是无 href 占位按钮）", () => {
    nav.pathname = "/covers";
    render(<SidebarNav items={navItems} />);
    // 已升为 Link → 无原占位 button；名称带后缀；当前路由高亮。
    expect(screen.queryByRole("button", { name: /封面工坊/ })).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "封面工坊（即将上线）" });
    expect(link).toHaveAttribute("href", "/covers");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
