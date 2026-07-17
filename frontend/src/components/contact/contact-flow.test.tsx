import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { markJustRegistered } from "@/lib/contact/welcome-flag";

import { ContactQr } from "./contact-qr";
import { WelcomeContactBanner } from "./welcome-contact-banner";

// LANDING-CONTACT-UI-0001 · 联系链路承重。
// 转化链路的断点是「落地页说联系我们，却没给联系方式」；这里钉的是接上之后**每一环都真的通**：
// 二维码可见且 alt 有意义 → 注册标记 → 横幅出现 → 弹窗有码 → 关闭可再找到（顶栏常驻入口另测）。

afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("ContactQr（二维码 + 双端引导）", () => {
  it("二维码图存在，alt 说清「这是什么 + 用来干什么」（读屏用户扫不了码，空 alt = 对他们不存在）", () => {
    render(<ContactQr />);
    const img = screen.getByRole("img", { name: copy.contact.qrAlt });
    expect(img).toHaveAttribute("src", "/wechat-qr.png");
    // alt 必须包含「怎么用」的动作词，不是一句空的「二维码」
    expect(copy.contact.qrAlt).toContain("扫");
  });

  it("双端引导都渲染：PC「手机扫屏上的码」+ 移动端「保存图 → 微信扫一扫从相册选取」", () => {
    render(<ContactQr />);
    // 两段文案都在 DOM（显隐由 sm: 断点 CSS 决定，jsdom 不算样式 → 钉存在性；断点显隐由 e2e 真浏览器钉）
    expect(screen.getByText(copy.contact.hintDesktop)).toBeInTheDocument();
    expect(screen.getByText(copy.contact.hintMobile)).toBeInTheDocument();
  });

  // 🔴 证伪任务包 §零 的固化：实测 u.wechat.com 对四种 UA（桌面/iPhone/Android/微信内）一律
  // 301 → wechat.com 官网，且无 Universal Links/App Links 配置 →「点链接唤起微信」不成立。
  // 移动端唯一可靠路径 = 保存图 → 微信扫一扫从相册识别。这条钉「保存」而不是「跳链接」：
  // 若有人好心把它改回 <a href="https://u.wechat.com/...">，本条必红 —— 那个链接只会把用户送去微信官网。
  it("🔴 移动端给的是「保存二维码」下载（download 属性 → 存图），不是跳 u.wechat.com 的链接", () => {
    render(<ContactQr />);
    const save = screen.getByRole("link", { name: new RegExp(copy.contact.saveQr) });
    expect(save).toHaveAttribute("href", "/wechat-qr.png");
    expect(save).toHaveAttribute("download");
    // 全组件不存在指向 u.wechat.com 的链接（实测它 301 到微信官网，放出来比没有更糟）
    const links = screen.getAllByRole("link");
    for (const a of links) expect(a.getAttribute("href")).not.toContain("u.wechat.com");
  });
});

describe("注册成功 → 工作台欢迎横幅（三条约束的承重）", () => {
  beforeEach(() => markJustRegistered()); // = register/page.tsx 成功分支落的标记

  it("标记在 → 横幅出现：标题 + 「查看微信二维码」动作 + 「我知道了」关闭；无标记 → 不渲染", () => {
    const { unmount } = render(<WelcomeContactBanner />);
    expect(screen.getByText(copy.contact.welcomeTitle)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.contact.welcomeAction })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.contact.welcomeDismiss })).toBeInTheDocument();
    unmount();

    localStorage.clear(); // 没注册过（或已关过）→ 老用户不被打扰
    render(<WelcomeContactBanner />);
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
  });

  // 约束 1「不一闪而过」：横幅没有定时器 —— 重新挂载（= 刷新页面）它还在，直到用户主动关。
  it("🔴 不一闪而过：卸载重挂（= 用户刷新）横幅仍在 —— 只有主动关闭才消失", () => {
    const first = render(<WelcomeContactBanner />);
    expect(screen.getByText(copy.contact.welcomeTitle)).toBeInTheDocument();
    first.unmount();

    render(<WelcomeContactBanner />);
    expect(screen.getByText(copy.contact.welcomeTitle)).toBeInTheDocument();
  });

  it("点「查看微信二维码」→ 弹窗打开，里面真的有二维码图（不是空弹窗）", () => {
    render(<WelcomeContactBanner />);
    fireEvent.click(screen.getByRole("button", { name: copy.contact.welcomeAction }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("img", { name: copy.contact.qrAlt })).toHaveAttribute("src", "/wechat-qr.png");
  });

  // 约束 2「不阻断」：横幅本身不是弹窗；弹窗只在用户主动点开后出现，且能关。
  it("不阻断：初始无 dialog；打开后可关闭（Esc/关闭钮均可，这里钉关闭钮）", () => {
    render(<WelcomeContactBanner />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: copy.contact.welcomeAction }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: copy.historyImages.close }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // 约束 3 前半：主动关闭 → 标记清除 → 重挂不再出现（不纠缠用户）。
  // 后半（关掉之后还能找到联系方式）在 top-bar 常驻入口的测试里钉。
  it("点「我知道了」→ 横幅消失且标记清除：重挂载不再出现", () => {
    const first = render(<WelcomeContactBanner />);
    fireEvent.click(screen.getByRole("button", { name: copy.contact.welcomeDismiss }));
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
    first.unmount();

    render(<WelcomeContactBanner />);
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
  });
});
