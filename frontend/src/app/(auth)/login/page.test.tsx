import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { copy } from "@/lib/copy";

// AUTH-UI-0001 · 登录页文案改动：「用户标识 (tenant slug)」→「用户名」、删副标题「输入用户与账号以继续」、
// 加「去注册」互链。字段/登录逻辑不动（仍 tenantSlug+email+password）。
const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const auth = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@/lib/auth/auth-context", () => auth);

import LoginPage from "./page";

const login = vi.fn();
afterEach(() => vi.clearAllMocks());

describe("LoginPage (文案改动)", () => {
  it("label 为「用户名」，不再有「用户标识 (tenant slug)」；副标题已删", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, login });
    render(<LoginPage />);
    expect(screen.getByText(copy.auth.usernameLabel)).toBeInTheDocument();
    expect(screen.queryByText(/tenant slug/i)).not.toBeInTheDocument();
    expect(screen.queryByText("输入用户与账号以继续")).not.toBeInTheDocument();
  });

  it("「没有账号？去注册」链接 → /register", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, login });
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: copy.auth.loginToRegister })).toHaveAttribute("href", "/register");
  });

  it("提交仍以 tenantSlug+email+password 调 login（逻辑零回归）", async () => {
    login.mockResolvedValue(undefined);
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, login });
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(copy.auth.usernameLabel), { target: { value: "huading" } });
    fireEvent.change(screen.getByLabelText(copy.auth.emailLabel), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText(copy.auth.passwordLabel), { target: { value: "pw123456" } });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.loginSubmit }));
    await waitFor(() =>
      expect(login).toHaveBeenCalledWith({ tenantSlug: "huading", email: "a@b.com", password: "pw123456" })
    );
  });
});
