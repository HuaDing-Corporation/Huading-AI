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
