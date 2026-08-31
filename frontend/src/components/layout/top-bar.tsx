"use client";

import { useState } from "react";
import Link from "next/link";
import { Bell, LogOut, MessageCircle, Search, Settings, ShieldCheck } from "lucide-react";

import { ContactDialog } from "@/components/contact/contact-dialog";
import { QuotaBadge } from "@/components/layout/quota-badge";
import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Glass } from "@/components/ui/glass";
import { Input } from "@/components/ui/input";
import { Logo } from "@/components/ui/logo";
import { useAuth } from "@/lib/auth/auth-context";
import { canUseAdminConsole } from "@/lib/auth/vip";
import { copy } from "@/lib/copy";

export function TopBar() {
  const { session, logout } = useAuth();
  // LANDING-CONTACT-UI-0001：「开通额度」**常驻**入口 → 客服微信二维码弹窗。
  // 它是注册欢迎横幅「能再次找到」约束的兜底：横幅可以关，这个入口永远在（不依赖注册标记、
  // 不依赖 quota 数据 —— QuotaBadge 没数据时不渲染，所以不能挂在它身上）。
  const [contactOpen, setContactOpen] = useState(false);
  const displayName = session?.user?.user.full_name ?? session?.user?.user.email ?? "华";
  const initial = displayName.trim().slice(0, 1).toUpperCase() || "华";
  // 管理后台入口（ADMIN-CONSOLE-UI-0001）：仅 permissions 含 admin_console（平台租户）显示——非 role 判据。
  const showAdmin = canUseAdminConsole(session);

  return (
    // 窄屏优先保留管理员/余额/开通额度/退出/头像五个关键操作，移动端隐藏装饰性品牌标；
    // 品牌身份由当前页面标题与导航上下文承接。
    // 真实 mock 余额 844/1000 会占 107px；320px 下五项动作超过单行可用宽度，因此允许紧凑布局换行。
    // 完整品牌、搜索与文字到 lg 恢复；lg 允许动作区换行，xl 空间充足时恢复单行。
    // e2e/landing 会先等异步 QuotaBadge 落屏再量断点精确值，避免余额尚未出现时假绿。
    <Glass className="col-span-full flex flex-wrap items-center gap-2 rounded-card px-3 py-[15px] sm:gap-[18px] sm:px-6">
      <div className="hidden flex-none lg:block">
        <Logo />
      </div>

      <div className="relative mx-2 hidden max-w-[440px] flex-1 lg:block">
        <Search
          className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint"
          size={18}
          strokeWidth={1.8}
        />
        <Input
          aria-label="搜索"
          placeholder="搜索项目、模板、任务…"
          className="rounded-[15px] py-3 pl-11 text-[13.5px]"
        />
      </div>

      <div className="ml-auto flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2 lg:basis-full lg:gap-3 xl:basis-auto xl:flex-none xl:flex-nowrap">
        {showAdmin && (
          <Link
            href="/admin"
            aria-label={copy.admin.consoleEntry}
            className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            <ShieldCheck size={15} strokeWidth={1.8} aria-hidden />
            {/* 移动端文字隐藏 + 图标 aria-hidden → 由 aria-label 兜可及名（P2-③） */}
            <span className="hidden lg:inline">{copy.admin.consoleEntry}</span>
          </Link>
        )}
        <QuotaBadge />
        {/* 常驻「开通额度」：紧挨余额徽标（语义关联——余额不够 → 在哪开通）。移动端收成图标（P2-③ 同款）。 */}
        <button
          type="button"
          onClick={() => setContactOpen(true)}
          aria-label={copy.contact.consoleEntry}
          className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold"
        >
          <MessageCircle size={15} strokeWidth={1.8} aria-hidden />
          <span className="hidden lg:inline">{copy.contact.consoleEntry}</span>
        </button>
        <ContactDialog open={contactOpen} onOpenChange={setContactOpen} />
        <Button variant="icon" size="icon" aria-label="通知" className="hidden lg:flex">
          <Bell size={18} strokeWidth={1.8} />
        </Button>
        <Button variant="icon" size="icon" aria-label="设置" className="hidden lg:flex">
          <Settings size={18} strokeWidth={1.8} />
        </Button>
        <Button variant="icon" size="icon" aria-label="退出登录" title="退出登录" onClick={logout}>
          <LogOut size={18} strokeWidth={1.8} />
        </Button>
        <Avatar>{initial}</Avatar>
      </div>
    </Glass>
  );
}
