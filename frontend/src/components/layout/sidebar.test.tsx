import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// 数据看板入口「始终显示」（ADMIN-VIP-GATE-UI-0001：去 adminOnly 隐藏，门禁移到页面友好页）。
const auth = vi.hoisted(() => ({ role: "admin" as string | undefined }));
vi.mock("@/lib/auth/auth-context", () => ({ useAuth: () => ({ session: auth.role ? { role: auth.role } : null }) }));
vi.mock("@/lib/api/hooks", () => ({ useQuota: () => ({ data: undefined }) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  )
}));

import { Sidebar } from "./sidebar";

afterEach(() => {
  auth.role = "admin";
});

describe("Sidebar (数据看板·始终显示 · ADMIN-VIP-GATE-UI-0001)", () => {
  it("管理员：显示「数据看板」入口（链接指向 /analytics）", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "数据看板" })).toHaveAttribute("href", "/analytics");
    expect(screen.getByRole("link", { name: "工作台" })).toBeInTheDocument();
  });

  // 承重：不再按 adminOnly 隐藏——非管理员也显示「数据看板」（点进去看 VIP 友好页，不在此隐藏）。
  it("非管理员（creator）：仍显示「数据看板」入口（去 adminOnly 隐藏）", () => {
    auth.role = "creator";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "数据看板" })).toHaveAttribute("href", "/analytics");
    expect(screen.getByRole("link", { name: "工作台" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "批量生产" })).toBeInTheDocument();
  });

  it("未登录（无 session）：同样显示「数据看板」入口", () => {
    auth.role = undefined;
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "数据看板" })).toHaveAttribute("href", "/analytics");
  });
});

// UI-COMINGSOON-TENANT-RENAME-0001 · 范围一：4 板块「（即将上线）」+ 可点占位路由，其余零回归。
describe("Sidebar · 板块「即将上线」占位 gate", () => {
  it("模板中心/品牌库/封面工坊/发布中心/团队：导航名带「（即将上线）」且为可点 Link（指向各占位路由）", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "模板中心（即将上线）" })).toHaveAttribute("href", "/templates");
    expect(screen.getByRole("link", { name: "品牌库（即将上线）" })).toHaveAttribute("href", "/brand-library");
    // 封面工坊纳入 gate（UI-COMINGSOON-COVER-0001）
    expect(screen.getByRole("link", { name: "封面工坊（即将上线）" })).toHaveAttribute("href", "/covers");
    expect(screen.getByRole("link", { name: "发布中心（即将上线）" })).toHaveAttribute("href", "/publish");
    expect(screen.getByRole("link", { name: "团队（即将上线）" })).toHaveAttribute("href", "/team");
  });

  it("零回归：工作台/批量生产/数据看板 无「（即将上线）」后缀，照常可点", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "工作台" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "批量生产" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "数据看板" })).toBeInTheDocument();
    // 真板块不带后缀
    expect(screen.queryByText("工作台（即将上线）")).not.toBeInTheDocument();
  });

  // HISTORY-IMAGE-TAB-UI-0001：左侧「图片历史」下线（并进工作台图片 tab）——导航里不再有该入口。
  it("承重：图片历史入口已下线，左导航不再出现", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.queryByRole("link", { name: "图片历史" })).not.toBeInTheDocument();
  });
});
