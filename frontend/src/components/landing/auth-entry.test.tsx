import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { copy } from "@/lib/copy";

// LANDING-ENTRY-UI-0001 · 右上角入口两态（冻结 §一.2）：未登录=登录/立即注册；已登录=首字头像 +
// 下拉（进控制台/退出登录，Esc 关闭）。mock auth-context 切态。
const auth = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@/lib/auth/auth-context", () => auth);

import { AuthEntry } from "./auth-entry";

const logout = vi.fn();
const authedSession = {
  token: "t",
  tenantId: "tid",
  userId: "uid",
  role: "member",
  user: { user: { full_name: "陈大文", email: "chen@x.com" } }
};

afterEach(() => vi.clearAllMocks());

describe("AuthEntry (入口两态)", () => {
  it("未登录：显示「登录」(/login) + 「立即注册」(/register) 链接", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, logout });
    render(<AuthEntry />);
    expect(screen.getByRole("link", { name: copy.landing.login })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: copy.landing.register })).toHaveAttribute("href", "/register");
  });

  it("已登录：首字头像「陈」；点开下拉(disclosure) → 进控制台(/link) + 退出登录(button 触发 logout)", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: authedSession, ready: true, logout });
    render(<AuthEntry />);
    const trigger = screen.getByRole("button", { name: copy.landing.avatarAria });
    expect(trigger).toHaveTextContent("陈");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls"); // 关联下拉区
    expect(screen.getByRole("link", { name: copy.landing.menuConsole })).toHaveAttribute("href", "/");

    fireEvent.click(screen.getByRole("button", { name: copy.landing.menuLogout }));
    expect(logout).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("link", { name: copy.landing.menuConsole })).not.toBeInTheDocument(); // 点后关闭
  });

  it("已登录：Esc 关闭下拉，焦点回触发器（键盘用户不丢位置）", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: authedSession, ready: true, logout });
    render(<AuthEntry />);
    const trigger = screen.getByRole("button", { name: copy.landing.avatarAria });
    fireEvent.click(trigger);
    expect(screen.getByRole("link", { name: copy.landing.menuConsole })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("link", { name: copy.landing.menuConsole })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("无 full_name → 取 email 首字（大写）；ready=false → 等尺寸占位不闪按钮", () => {
    (auth.useAuth as Mock).mockReturnValue({
      session: { ...authedSession, user: { user: { full_name: null, email: "chen@x.com" } } },
      ready: true,
      logout
    });
    const { unmount } = render(<AuthEntry />);
    expect(screen.getByRole("button", { name: copy.landing.avatarAria })).toHaveTextContent("C");
    unmount();

    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: false, logout });
    render(<AuthEntry />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
