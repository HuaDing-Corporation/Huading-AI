"use client";

import Link from "next/link";
import { Bell, LogOut, Search, Settings, ShieldCheck } from "lucide-react";

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
            className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            <ShieldCheck size={15} strokeWidth={1.8} aria-hidden />
            <span className="hidden sm:inline">{copy.admin.consoleEntry}</span>
          </Link>
        )}
        <QuotaBadge />
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
