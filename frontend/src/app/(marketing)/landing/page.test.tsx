import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";

// LANDING-ENTRY-UI-0001 · 落地页结构（冻结 §三）：Hero(流光标题/双CTA/可信数据条) + 7+1 模块卡 +
// 4 样片占位 + 5 步 + 注册 CTA + 页脚。auth mock 为未登录（入口两态细节在 auth-entry.test）。
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: () => ({ session: null, ready: true, logout: vi.fn() })
}));

import LandingPage from "./page";

describe("LandingPage (/landing 落地页)", () => {
  it("Hero：h1 主标题 + 副标 + 双 CTA(注册/登录) + 数据条（不含不实「500+」）", () => {
    render(<LandingPage />);
    expect(screen.getByRole("heading", { level: 1, name: copy.landing.heroTitle })).toBeInTheDocument();
    expect(screen.getByText(copy.landing.heroSub)).toBeInTheDocument();
    // 双 CTA：立即注册 → /register；登录控制台 → /login。
    const registers = screen.getAllByRole("link", { name: copy.landing.ctaRegister });
    expect(registers.length).toBeGreaterThanOrEqual(2); // Hero + 注册 CTA 区
    registers.forEach((a) => expect(a).toHaveAttribute("href", "/register"));
    expect(screen.getByRole("link", { name: copy.landing.ctaLogin })).toHaveAttribute("href", "/login");
    // 数据条：可信表述（冻结 §三※），不得出现「500+」。
    expect(screen.getByText(copy.landing.statModules)).toBeInTheDocument();
    expect(screen.getByText(copy.landing.statAuto)).toBeInTheDocument();
    expect(screen.getByText(copy.landing.statDistribute)).toBeInTheDocument();
    expect(screen.queryByText(/500\+/)).not.toBeInTheDocument();
  });

  // ADMIN-VIP-GATE-UI-0001：新注册余额=0 → 落地页不得承诺「免费」/「免费额度/免费体验/注册即可体验全部模块」。
  it("0 余额文案修正：CTA 无「免费」、注册 CTA 区改「注册后联系我们开通额度」、五步第 1 步点明开通额度", () => {
    const { container } = render(<LandingPage />);
    // 全页无「免费」承诺（去掉「立即免费注册」的「免费」暗示）。
    expect(container.textContent).not.toContain("免费");
    expect(screen.queryByText(/立即免费注册/)).not.toBeInTheDocument();
    // 注册 CTA 区新文案（不承诺免费体验）。
    expect(screen.getByText(copy.landing.ctaSub)).toBeInTheDocument();
    expect(screen.getByText("注册后联系我们开通额度")).toBeInTheDocument();
    expect(screen.queryByText(/注册即可体验全部模块|体验全部模块/)).not.toBeInTheDocument();
    // 五步第 1 步点明开通额度后才能生成。
    expect(screen.getByText(/开通额度后即可生成/)).toBeInTheDocument();
  });

  it("七大模块 + 「更多能力持续上线」= 8 卡；样片墙 4 占位；五步上手 5 条", () => {
    render(<LandingPage />);
    for (const name of [
      copy.landing.modAvatar,
      copy.landing.modVideoGen,
      copy.landing.modEcomImage,
      copy.landing.modPhoto,
      copy.landing.modCopywriting,
      copy.landing.modEcomVideo,
      copy.landing.modReverse,
      copy.landing.modMore
    ]) {
      expect(screen.getByRole("heading", { level: 3, name })).toBeInTheDocument();
    }
    // 样片墙：4 占位位（口播/电商图/带货/AI 图），标明占位待替换。
    expect(screen.getByText(copy.landing.sampleAvatar)).toBeInTheDocument();
    expect(screen.getByText(copy.landing.sampleEcomVideo)).toBeInTheDocument();
    expect(screen.getAllByText(copy.landing.samplePlaceholder)).toHaveLength(4);
    // 五步。
    for (const step of [copy.landing.step1, copy.landing.step5]) {
      expect(screen.getByText(step)).toBeInTheDocument();
    }
  });

  it("顶栏：品牌字标 + 锚点(功能/样片/教学 → #modules/#samples/#steps) + 未登录入口；页脚 © + 备案占位", () => {
    render(<LandingPage />);
    expect(screen.getByRole("link", { name: copy.landing.brand })).toHaveAttribute("href", "/landing");
    const nav = screen.getByRole("navigation", { name: copy.landing.navAria });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: copy.landing.navFeatures })).toHaveAttribute("href", "#modules");
    expect(screen.getByRole("link", { name: copy.landing.navSamples })).toHaveAttribute("href", "#samples");
    expect(screen.getByRole("link", { name: copy.landing.navSteps })).toHaveAttribute("href", "#steps");
    expect(screen.getByRole("link", { name: copy.landing.login })).toBeInTheDocument();
    expect(screen.getByText(copy.landing.footerCopyright)).toBeInTheDocument();
    expect(screen.getByText(copy.landing.footerIcp)).toBeInTheDocument();
  });

  it("标题层级 h1 唯一；主内容 skip-link 可达", () => {
    render(<LandingPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: copy.landing.skipToMain })).toHaveAttribute("href", "#landing-main");
  });
});
