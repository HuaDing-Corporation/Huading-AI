import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// 数据看板入口「仅管理员显示」（ANALYTICS-UI-0001）。
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

describe("Sidebar (数据看板·仅管理员)", () => {
  it("管理员：显示「数据看板」入口（链接指向 /analytics）", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "数据看板" })).toHaveAttribute("href", "/analytics");
    expect(screen.getByRole("link", { name: "工作台" })).toBeInTheDocument();
  });

  it("非管理员（creator）：不显示「数据看板」，其余项照常（承重·仅管理员过滤）", () => {
    auth.role = "creator";
    render(<Sidebar />);
    expect(screen.queryByText("数据看板")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "工作台" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "批量生产" })).toBeInTheDocument();
  });

  it("未登录（无 session）：同样不显示「数据看板」", () => {
    auth.role = undefined;
    render(<Sidebar />);
    expect(screen.queryByText("数据看板")).not.toBeInTheDocument();
  });
});

// UI-COMINGSOON-TENANT-RENAME-0001 · 范围一：4 板块「（即将上线）」+ 可点占位路由，其余零回归。
describe("Sidebar · 板块「即将上线」占位 gate", () => {
  it("模板中心/品牌库/发布中心/团队：导航名带「（即将上线）」且为可点 Link（指向各占位路由）", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "模板中心（即将上线）" })).toHaveAttribute("href", "/templates");
    expect(screen.getByRole("link", { name: "品牌库（即将上线）" })).toHaveAttribute("href", "/brand-library");
    expect(screen.getByRole("link", { name: "发布中心（即将上线）" })).toHaveAttribute("href", "/publish");
    expect(screen.getByRole("link", { name: "团队（即将上线）" })).toHaveAttribute("href", "/team");
  });

  it("零回归：工作台/批量生产/数据看板 无「（即将上线）」后缀；封面工坊保持原占位（不带后缀、非链接）", () => {
    auth.role = "admin";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "工作台" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "批量生产" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "数据看板" })).toBeInTheDocument();
    // 封面工坊：未加入 coming-soon gate → 无后缀、仍是不可点占位（非 link）
    expect(screen.getByText("封面工坊")).toBeInTheDocument();
    expect(screen.queryByText("封面工坊（即将上线）")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "封面工坊" })).not.toBeInTheDocument();
    // 真板块不带后缀
    expect(screen.queryByText("工作台（即将上线）")).not.toBeInTheDocument();
  });
});
