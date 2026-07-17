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
    <Glass className="col-span-full flex items-center gap-3 rounded-card px-4 py-[15px] sm:gap-[18px] sm:px-6">
      <Logo />

      <div className="relative mx-2 hidden max-w-[440px] flex-1 sm:block">
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

      <div className="ml-auto flex items-center gap-2.5 sm:gap-3">
        {showAdmin && (
          <Link
            href="/admin"
            aria-label={copy.admin.consoleEntry}
            className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            <ShieldCheck size={15} strokeWidth={1.8} aria-hidden />
            {/* 移动端文字隐藏 + 图标 aria-hidden → 由 aria-label 兜可及名（P2-③） */}
            <span className="hidden sm:inline">{copy.admin.consoleEntry}</span>
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
          <span className="hidden sm:inline">{copy.contact.consoleEntry}</span>
        </button>
        <ContactDialog open={contactOpen} onOpenChange={setContactOpen} />
        <Button variant="icon" size="icon" aria-label="通知" className="hidden sm:flex">
          <Bell size={18} strokeWidth={1.8} />
        </Button>
        <Button variant="icon" size="icon" aria-label="设置" className="hidden sm:flex">
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
