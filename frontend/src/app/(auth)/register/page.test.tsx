import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy";

// AUTH-UI-0001 · 注册页：5 字段 + 客户端 friendly 校验 + 提交成功进控制台 + slug 占用友好错误 + 去登录互链。
const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const auth = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@/lib/auth/auth-context", () => auth);

import RegisterPage from "./page";

const register = vi.fn();

function fill(fields: Partial<{ slug: string; team: string; email: string; password: string; full: string }>) {
  if (fields.slug !== undefined) fireEvent.change(screen.getByLabelText(copy.auth.usernameLabel), { target: { value: fields.slug } });
  if (fields.team !== undefined) fireEvent.change(screen.getByLabelText(copy.auth.teamNameLabel), { target: { value: fields.team } });
  if (fields.email !== undefined) fireEvent.change(screen.getByLabelText(copy.auth.emailLabel), { target: { value: fields.email } });
  if (fields.password !== undefined) fireEvent.change(screen.getByLabelText(copy.auth.passwordLabel), { target: { value: fields.password } });
  if (fields.full !== undefined) fireEvent.change(screen.getByLabelText(copy.auth.fullNameLabel), { target: { value: fields.full } });
}

afterEach(() => vi.clearAllMocks());

describe("RegisterPage (注册)", () => {
  it("非法用户名（大写/非法字符）→ friendly 校验拦截，不发请求", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "Bad_Slug", team: "华鼎", email: "a@b.com", password: "pw123456" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    expect(screen.getByText(copy.auth.errUsername)).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it("密码不足 8 位 → friendly 校验拦截", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "huading", team: "华鼎", email: "a@b.com", password: "short" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    expect(screen.getByText(copy.auth.errPassword)).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  // FIX1 承重（Codex B P1）：fullName 选填但超长(201 字)必须提交前拦截、不调 register。
  it("姓名 201 字（超 BE 200 上界）→ friendly 拦截，不调 register", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "huading", team: "华鼎", email: "a@b.com", password: "pw123456", full: "名".repeat(201) });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    expect(screen.getByText(copy.auth.errFullName)).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  // 边界：正好 200 字合法（不拦截，正常提交）。
  it("姓名 200 字（=上界）→ 合法，正常调 register", async () => {
    register.mockResolvedValue(undefined);
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "huading", team: "华鼎", email: "a@b.com", password: "pw123456", full: "名".repeat(200) });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    await waitFor(() => expect(register).toHaveBeenCalledTimes(1));
    expect((register.mock.calls[0][0] as { fullName?: string }).fullName).toHaveLength(200);
  });

  it("合法提交 → register(五字段，含 fullName) + 进控制台 /", async () => {
    register.mockResolvedValue(undefined);
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "huading", team: "华鼎科技", email: "a@b.com", password: "pw123456", full: "陈大文" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    await waitFor(() =>
      expect(register).toHaveBeenCalledWith({
        tenantSlug: "huading",
        tenantName: "华鼎科技",
        email: "a@b.com",
        password: "pw123456",
        fullName: "陈大文"
      })
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  // LANDING-CONTACT-UI-0001：注册成功 → 落 localStorage 标记 → 工作台首屏显示「联系开通额度」横幅。
  // 跳转行为**原样**（上一条钉着 replace("/")）—— 提示由控制台侧读标记显示，注册页不拦。
  it("🔴 注册成功 → 落欢迎横幅标记（hd:welcome-contact）；注册失败 → 不落", async () => {
    localStorage.clear();
    register.mockResolvedValue(undefined);
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    const ok = render(<RegisterPage />);
    fill({ slug: "huading", team: "华鼎", email: "a@b.com", password: "pw123456" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(localStorage.getItem("hd:welcome-contact")).toBe("1");
    ok.unmount();

    // 失败分支：register 抛错 → 不落标记（没注册成功就别欢迎人家）
    localStorage.clear();
    register.mockRejectedValue(new ApiError("boom", "err", 500));
    render(<RegisterPage />);
    fill({ slug: "huading2", team: "华鼎", email: "a@b.com", password: "pw123456" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    expect(await screen.findByText(copy.auth.errRegisterFailed)).toBeInTheDocument();
    expect(localStorage.getItem("hd:welcome-contact")).toBeNull();
  });

  it("姓名留空 → fullName 传 undefined（选填）", async () => {
    register.mockResolvedValue(undefined);
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "huading", team: "华鼎科技", email: "a@b.com", password: "pw123456" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    await waitFor(() => expect(register).toHaveBeenCalled());
    expect((register.mock.calls[0][0] as { fullName?: string }).fullName).toBeUndefined();
  });

  it("slug 已占用(409) → friendly 中文，不泄裸串，不跳转", async () => {
    register.mockRejectedValue(new ApiError("Tenant slug is already taken.", "tenant_slug_taken", 409));
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    fill({ slug: "taken", team: "华鼎", email: "a@b.com", password: "pw123456" });
    fireEvent.click(screen.getByRole("button", { name: copy.auth.registerSubmit }));
    expect(await screen.findByText(copy.auth.errSlugTaken)).toBeInTheDocument();
    expect(screen.queryByText(/taken\./i)).not.toBeInTheDocument(); // 不泄英文原串
    expect(replace).not.toHaveBeenCalled();
  });

  it("「已有账号？去登录」链接 → /login", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true, register });
    render(<RegisterPage />);
    expect(screen.getByRole("link", { name: copy.auth.registerToLogin })).toHaveAttribute("href", "/login");
  });
});
