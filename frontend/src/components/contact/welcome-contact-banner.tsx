"use client";

import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";

import { ContactDialog } from "@/components/contact/contact-dialog";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth/auth-context";
import { clearWelcomePending, hasWelcomePending } from "@/lib/contact/welcome-flag";
import { copy } from "@/lib/copy";

/**
 * 注册成功欢迎横幅（LANDING-CONTACT-UI-0001）——转化关键一秒：用户刚进工作台，
 * 马上会发现 0 余额什么都跑不动，这条横幅先把「为什么 + 怎么办」递到眼前。
 *
 * 任务包 §二.2 三条约束的落法：
 *  1. **不一闪而过** —— 不是 toast（toast 3–5s 自动消失，正撞枪口）：横幅常显，
 *     标记在 localStorage，刷新页面都还在，直到用户**主动**关闭；
 *  2. **不阻断** —— 横幅不是弹窗，工作台全部可操作；「查看二维码」才开弹窗，且随时可关；
 *  3. **能再次找到** —— 关闭横幅后，顶栏的「开通额度」**常驻**入口（TopBar）打开同一个弹窗。
 *     常驻入口不依赖注册标记，对所有 0 余额账号（不只新注册）都在。
 *
 * 挂载时读一次 localStorage（避免 SSR/hydration 不一致：初始 false，effect 后显示）。
 * 🔴 标记绑注册者 email、读时比对**当前 session**（review 抓的串号 bug）：同一浏览器上
 * A 注册没关横幅就退出、B 登录 —— B 不该看到「注册成功，欢迎加入华鼎！」。
 */
export function WelcomeContactBanner() {
  const { session } = useAuth();
  const email = session?.user?.user.email;
  const [visible, setVisible] = useState(false);
  const [qrOpen, setQrOpen] = useState(false);

  useEffect(() => {
    if (hasWelcomePending(email)) setVisible(true);
  }, [email]);

  if (!visible) return null;

  const dismiss = () => {
    clearWelcomePending();
    setVisible(false);
  };

  const C = copy.contact;
  return (
    <div
      role="status"
      className="glass flex flex-wrap items-center gap-3 rounded-card border border-line-gold px-4 py-3.5"
    >
      <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-grad-gold text-ink shadow-avatar">
        <Sparkles size={16} strokeWidth={1.8} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <b className="block text-[14px] font-semibold text-ink">{C.welcomeTitle}</b>
        <p className="mt-0.5 text-[12.5px] leading-[1.6] text-ink-soft">{C.welcomeBody}</p>
      </div>
      <div className="flex flex-none items-center gap-2">
        <Button variant="primary" size="sm" onClick={() => setQrOpen(true)}>
          {C.welcomeAction}
        </Button>
        {/* 「我知道了」= 主动关闭 → 清标记。之后靠顶栏常驻「开通额度」找回。
            关闭钮用既有 ui/button 的 ghost/icon —— 上一版这里是手写样式串，且与同一 PR 里
            contact-dialog 那份**当场就漂了**（h-9/rounded-field/text-ink-faint vs h-8/rounded-mark/
            text-ink-soft）。拷贝不只是变多，它会悄悄漂移 —— 本包不留手写关闭钮。 */}
        <Button variant="ghost" size="icon" onClick={dismiss} aria-label={C.welcomeDismiss} title={C.welcomeDismiss}>
          <X size={16} strokeWidth={2} />
        </Button>
      </div>
      <ContactDialog open={qrOpen} onOpenChange={setQrOpen} />
    </div>
  );
}
