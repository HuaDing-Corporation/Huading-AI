"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { LayoutDashboard, LogOut } from "lucide-react";

import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth/auth-context";
import { copy } from "@/lib/copy";

/**
 * 右上角登录入口两态 (LANDING-ENTRY-UI-0001 · 冻结 §一.2)：
 *  - 未登录：「登录」soft 按钮 + 「立即注册」primary 按钮；
 *  - 已登录：首字圆标头像（复用 ui/Avatar，取 full_name/email 首字，同 TopBar 规则），
 *    点开下拉：进控制台(/) / 退出登录。
 * a11y（FIX：采 disclosure 而非 menu widget——仅两项、Tab 线性导航即够，避免声明 role=menu 却缺 APG
 * 方向键模式的空契约）：触发器 aria-expanded + aria-controls；Esc / 点外部关闭，Esc 关闭后**焦点回触发器**
 * （键盘用户不丢位置）；下拉内图标 aria-hidden。登录态复用 auth-context；ready 前渲染等尺寸占位防 CLS。
 */
export function AuthEntry() {
  const { session, ready, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  // 下拉开启期间：点外部关闭（静默）；Esc 关闭并把焦点交还触发器。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!ready) return <div aria-hidden className="h-10 w-10" />;

  if (!session) {
    return (
      <div className="flex items-center gap-2">
        <Button asChild variant="soft" size="sm">
          <Link href="/login">{copy.landing.login}</Link>
        </Button>
        <Button asChild size="sm">
          {/* /register 由 AUTH-UI-0001 建：先占位链接；prefetch=false 免 404 预取噪音 */}
          <Link href="/register" prefetch={false}>
            {copy.landing.register}
          </Link>
        </Button>
      </div>
    );
  }

  const displayName = session.user?.user.full_name ?? session.user?.user.email ?? "华";
  const initial = displayName.trim().slice(0, 1).toUpperCase() || "华";
  const itemClass =
    "flex w-full items-center gap-2 rounded-field px-3 py-2.5 text-left text-[13px] text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold";

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-label={copy.landing.avatarAria}
        aria-controls={open ? menuId : undefined}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="rounded-full outline-none transition-transform focus-visible:shadow-focus-gold active:scale-[.97]"
      >
        <Avatar>{initial}</Avatar>
      </button>
      {open && (
        <div id={menuId} className="glass absolute right-0 top-12 z-50 min-w-[168px] rounded-panel p-1.5">
          <Link href="/" className={itemClass} onClick={() => setOpen(false)}>
            <LayoutDashboard size={15} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
            {copy.landing.menuConsole}
          </Link>
          <button
            type="button"
            className={itemClass}
            onClick={() => {
              setOpen(false);
              logout();
            }}
          >
            <LogOut size={15} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
            {copy.landing.menuLogout}
          </button>
        </div>
      )}
    </div>
  );
}
