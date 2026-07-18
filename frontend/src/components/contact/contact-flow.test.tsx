import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copy } from "@/lib/copy";
import { markJustRegistered } from "@/lib/contact/welcome-flag";

// 横幅要比对「标记里的注册者」与「当前 session」（串号防护）→ mock useAuth 供身份。
const auth = vi.hoisted(() => ({ useAuth: vi.fn() }));
vi.mock("@/lib/auth/auth-context", () => auth);

import { ContactQr } from "./contact-qr";
import { WelcomeContactBanner } from "./welcome-contact-banner";
import type { Mock } from "vitest";

// LANDING-CONTACT-UI-0001 · 联系链路承重。
// 转化链路的断点是「落地页说联系我们，却没给联系方式」；这里钉的是接上之后**每一环都真的通**：
// 二维码可见且 alt 有意义 → 注册标记 → 横幅出现 → 弹窗有码 → 关闭可再找到（顶栏常驻入口另测）。

// FIX1：身份 = tenantId + userId（不是 email）。email 只用于「同 email 不同 tenant」这条坏路径的构造。
const A = { tenantId: "tenant-A", userId: "user-A" };
const B = { tenantId: "tenant-B", userId: "user-B" };
const sessionOf = (id: { tenantId: string; userId: string }, email = "x@y.com") => ({
  token: "t",
  tenantId: id.tenantId,
  userId: id.userId,
  role: "admin",
  user: { user: { email, full_name: null } }
});

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

  it("双端引导都渲染：PC「手机扫屏上的码」+ 移动端「保存图 → 微信扫一扫从相册选取」+ iOS 长按兜底", () => {
    render(<ContactQr />);
    // 各段文案都在 DOM（显隐由 sm: 断点 CSS 决定，jsdom 不算样式 → 钉存在性；断点显隐由 e2e 真浏览器钉）
    expect(screen.getByText(copy.contact.hintDesktop)).toBeInTheDocument();
    expect(screen.getByText(copy.contact.hintMobile)).toBeInTheDocument();
    // 🔴 P2-1：iOS 兜底 —— iPhone Safari 的 download 进「文件」不进「照片」，长按存相册才可靠。
    expect(screen.getByText(copy.contact.hintSaveIos)).toBeInTheDocument();
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
  beforeEach(() => {
    markJustRegistered(A.tenantId, A.userId); // = register/page.tsx 成功分支落的标记（按身份）
    (auth.useAuth as Mock).mockReturnValue({ session: sessionOf(A), ready: true });
  });

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
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: copy.common.close }));
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

  it("未登录（session 空）→ 不显示（没有身份可比对就不显示，不猜）", () => {
    (auth.useAuth as Mock).mockReturnValue({ session: null, ready: true });
    render(<WelcomeContactBanner />);
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
  });
});

// ── FIX1：Codex B 核出的四条坏路径，各一条承重（身份 = tenantId+userId，非 email）─────────────
describe("欢迎横幅 · 身份四条坏路径（FIX1）", () => {
  // ① 同 email、不同 tenant：BE 唯一约束是 (tenant_id, email)，同 email 可注册多个租户。
  // A 注册（tenant-A），B 是**同一个 email 但 tenant-B** → B 不该看到 A 的欢迎。
  it("🔴 ① 同 email、不同 tenant → 看不到对方的横幅", () => {
    markJustRegistered(A.tenantId, A.userId);
    (auth.useAuth as Mock).mockReturnValue({
      session: sessionOf(B, "same@email.com"), // 同 email、不同 tenant/user
      ready: true
    });
    render(<WelcomeContactBanner />);
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
  });

  // ② 大写 email：旧实现用 email 绑定 + BE 转小写 → 大写永不匹配、横幅永不出现。
  // 身份用 tenantId/userId 后与 email 大小写无关 → 标记在、身份匹配 → 横幅照常出现。
  // （若有人退回 email 绑定，本条会红：markJustRegistered 存的是身份，email 版 hasWelcomePending 读不到。）
  it("🔴 ② session 的 email 是大写 → 横幅仍出现（身份不看 email，大小写不再吃掉横幅）", () => {
    markJustRegistered(A.tenantId, A.userId);
    (auth.useAuth as Mock).mockReturnValue({ session: sessionOf(A, "A@B.COM"), ready: true });
    render(<WelcomeContactBanner />);
    expect(screen.getByText(copy.contact.welcomeTitle)).toBeInTheDocument();
  });

  // ③ session 换人横幅要消失：同一挂载实例，session 从 A（有标记）切到 B（无标记）→ 横幅隐。
  // 旧实现 `if (hasWelcomePending) setVisible(true)` 只置 true、不置回 false → 换人后横幅不消失（红）。
  it("🔴 ③ session 换人（A→B，B 无标记）→ 横幅主动消失", () => {
    markJustRegistered(A.tenantId, A.userId);
    (auth.useAuth as Mock).mockReturnValue({ session: sessionOf(A), ready: true });
    const { rerender } = render(<WelcomeContactBanner />);
    expect(screen.getByText(copy.contact.welcomeTitle)).toBeInTheDocument();

    // 同一实例上把 session 换成 B（无标记）
    (auth.useAuth as Mock).mockReturnValue({ session: sessionOf(B), ready: true });
    rerender(<WelcomeContactBanner />);
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
  });

  // ④ 多标签页互删：A、B 两个身份的标记共存；A 关闭自己的横幅**只清 A 的 key** → B 的标记还在。
  // 单一设备级 key 会被后写覆盖、A 清除误删 B → B 登录看不到（红）。每身份分 key 修掉。
  it("🔴 ④ 多身份标记共存：A 关闭横幅只清 A → B 的标记不被误删", () => {
    markJustRegistered(A.tenantId, A.userId);
    markJustRegistered(B.tenantId, B.userId); // 另一标签页/另一账号也注册了

    // A 登录并关闭横幅
    (auth.useAuth as Mock).mockReturnValue({ session: sessionOf(A), ready: true });
    const asA = render(<WelcomeContactBanner />);
    fireEvent.click(screen.getByRole("button", { name: copy.contact.welcomeDismiss }));
    expect(screen.queryByText(copy.contact.welcomeTitle)).not.toBeInTheDocument();
    asA.unmount();

    // B 登录 → B 的标记没被 A 的清除动作误删 → 横幅仍在
    (auth.useAuth as Mock).mockReturnValue({ session: sessionOf(B), ready: true });
    render(<WelcomeContactBanner />);
    expect(screen.getByText(copy.contact.welcomeTitle)).toBeInTheDocument();
  });
});
