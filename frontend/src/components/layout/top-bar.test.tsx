import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// LANDING-CONTACT-UI-0001 · 顶栏常驻「开通额度」入口 —— 注册欢迎横幅「能再次找到」约束的兜底。
// 🔴 这个入口的三个「不依赖」是它成为兜底的资格，逐条钉死：
//   不依赖注册标记（老用户 0 余额也要用）/ 不依赖 quota 数据（QuotaBadge 没数据不渲染，它不能）/
//   不依赖横幅还在（横幅关了它还在 —— 这正是「能再次找到」）。
const auth = vi.hoisted(() => ({ useAuth: vi.fn(), }));
vi.mock("@/lib/auth/auth-context", () => auth);
const hooks = vi.hoisted(() => ({ useQuota: vi.fn() }));
vi.mock("@/lib/api/hooks", () => hooks);

import { TopBar } from "./top-bar";
import type { Mock } from "vitest";

afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

function renderBar() {
  (auth.useAuth as Mock).mockReturnValue({
    session: { token: "t", user: { user: { full_name: "陈大文", email: "a@b.com" } } },
    ready: true,
    logout: vi.fn()
  });
  return render(<TopBar />);
}

describe("TopBar · 常驻「开通额度」入口（能再次找到）", () => {
  it("🔴 无注册标记、无 quota 数据 → 入口仍在；点它 → 弹窗出现且里面有二维码", () => {
    hooks.useQuota.mockReturnValue({ data: undefined }); // QuotaBadge 不渲染的最坏情形
    renderBar();

    // localStorage 干净（≠ 刚注册）——入口不依赖任何标记
    fireEvent.click(screen.getByRole("button", { name: copy.contact.consoleEntry }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(copy.contact.dialogTitle)).toBeInTheDocument();
    expect(within(dialog).getByRole("img", { name: copy.contact.qrAlt })).toHaveAttribute("src", "/wechat-qr.png");
  });

  it("弹窗可关（关闭按钮）→ dialog 卸载；再点入口 → 还能打开（可反复找到）", () => {
    hooks.useQuota.mockReturnValue({ data: { remaining: 0, total: 0 } });
    renderBar();

    fireEvent.click(screen.getByRole("button", { name: copy.contact.consoleEntry }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: copy.common.close }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.contact.consoleEntry }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("零回归：余额徽标 / 退出登录等既有元素原样在", () => {
    hooks.useQuota.mockReturnValue({ data: { remaining: 3, total: 10 } });
    renderBar();
    expect(screen.getByText(/余额 3\/10/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
  });
});
