import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

// LANDING-ENTRY-UI-0001 · (app) 鉴权门改向：未登录 → replace("/landing")（不再 /login）；
// 已登录正常渲染 children（工作台零回归）；ready 前不跳转（防误伤）。
const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const auth = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@/lib/auth/auth-context", () => auth);

import AppLayout from "./layout";

afterEach(() => vi.clearAllMocks());

describe("AppLayout (鉴权门)", () => {
  it("未登录(ready) → replace('/landing')，渲染占位不闪控制台", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true });
    render(
      <AppLayout>
        <div data-testid="console" />
      </AppLayout>
    );
    expect(replace).toHaveBeenCalledWith("/landing");
    expect(screen.queryByTestId("console")).not.toBeInTheDocument();
  });

  it("已登录 → 渲染 children，不跳转（工作台零回归）", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: { token: "t" }, ready: true });
    render(
      <AppLayout>
        <div data-testid="console" />
      </AppLayout>
    );
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByTestId("console")).toBeInTheDocument();
  });

  it("ready=false → 不跳转、渲染 aria-busy 占位", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: false });
    render(
      <AppLayout>
        <div data-testid="console" />
      </AppLayout>
    );
    expect(replace).not.toHaveBeenCalled();
    expect(screen.queryByTestId("console")).not.toBeInTheDocument();
  });
});
