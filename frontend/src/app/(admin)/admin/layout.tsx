"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, ClipboardList, Gauge, LogOut, Mic2, PackageCheck, ReceiptText, ShieldAlert, Users } from "lucide-react";

import { Glass } from "@/components/ui/glass";
import { Logo } from "@/components/ui/logo";
import { useAuth } from "@/lib/auth/auth-context";
import { canUseAdminConsole } from "@/lib/auth/vip";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

// 管理员后台独立布局（ADMIN-CONSOLE-UI-0001）——不塞控制台侧边栏，自己的壳 + 左导航。
// 门禁：permissions 含 admin_console（= 平台租户，BE 同源派生）；**绝不看 session.role**（自助注册
// owner 全是 role=admin，线上 P0 教训）。无权限 → 友好页（BE 端点也会真 403 PLATFORM_ADMIN_REQUIRED，双保险）。
const NAV = [
  { href: "/admin/tenants", label: copy.admin.navTenants, icon: Users },
  { href: "/admin/brand-voice-orders", label: "人工音色订单", icon: PackageCheck },
  { href: "/admin/voice-slots", label: copy.admin.navVoiceSlots, icon: Mic2 },
  { href: "/admin/usage", label: copy.admin.navUsage, icon: ReceiptText },
  { href: "/admin/tasks", label: copy.admin.navTasks, icon: Gauge },
  { href: "/admin/audit", label: copy.admin.navAudit, icon: ClipboardList }
] as const;

function GateDenied() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div
        className="flex max-w-md flex-col items-center gap-3 rounded-card border border-line-gold bg-glass-fill px-8 py-14 text-center"
        role="status"
        aria-live="polite"
      >
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-glass-soft text-gold-deep">
          <ShieldAlert size={26} strokeWidth={1.8} aria-hidden />
        </span>
        <h1 className="text-[17px] font-semibold text-ink">{copy.admin.gateTitle}</h1>
        <p className="text-[13.5px] leading-relaxed text-ink-soft">{copy.admin.gateDesc}</p>
        <Link
          href="/"
          className="mt-1 rounded-field border border-line-gold bg-glass-fill px-4 py-2 text-[13px] text-gold-deep hover:bg-glass-hover"
        >
          {copy.admin.gateBack}
        </Link>
      </div>
    </main>
  );
}

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { session, ready, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (ready && !session) router.replace("/landing");
  }, [ready, session, router]);

  if (!ready || !session) return <main className="min-h-screen" aria-busy="true" />;
  if (!canUseAdminConsole(session)) return <GateDenied />;

  return (
    <main className="min-h-screen p-5 md:p-7">
      <div className="mx-auto grid max-w-[1600px] grid-cols-1 grid-rows-[auto_1fr] gap-5 md:grid-cols-[220px_minmax(0,1fr)]">
        {/* 后台自有顶栏：Logo + 「管理后台」徽标 + 返回控制台 + 退出 */}
        <Glass className="col-span-full flex flex-wrap items-center gap-3 rounded-card px-4 py-[13px] sm:px-6">
          <Logo />
          <span className="flex-none whitespace-nowrap rounded-pill border border-line-gold bg-glass-soft px-2.5 py-1 text-[11.5px] font-medium text-gold-deep">
            {copy.admin.consoleTitle}
          </span>
          <div className="flex w-full items-center justify-end gap-2 sm:ml-auto sm:w-auto">
            <Link
              href="/"
              className="inline-flex flex-none items-center gap-1.5 whitespace-nowrap rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-ink-soft hover:bg-glass-hover"
            >
              <ArrowLeft size={14} strokeWidth={2} aria-hidden /> {copy.admin.backToWorkbench}
            </Link>
            <button
              type="button"
              onClick={logout}
              aria-label="退出登录"
              title="退出登录"
              className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-field text-ink-soft hover:bg-glass-hover"
            >
              <LogOut size={16} strokeWidth={1.8} />
            </button>
          </div>
        </Glass>

        {/* 左导航（桌面）/ 顶部横滚（移动端） */}
        <Glass className="flex flex-row gap-1 overflow-x-auto rounded-card p-3 md:flex-col md:overflow-visible">
          <nav aria-label="后台导航" className="flex flex-row gap-1 md:flex-col">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "inline-flex flex-none items-center gap-2 rounded-field px-3 py-2 text-[13px] outline-none focus-visible:shadow-focus-gold",
                    active ? "bg-glass-soft font-medium text-gold-deep" : "text-ink-soft hover:bg-glass-hover hover:text-ink"
                  )}
                >
                  <Icon size={15} strokeWidth={1.8} aria-hidden />
                  {label}
                </Link>
              );
            })}
          </nav>
        </Glass>

        <section className="flex min-w-0 flex-col gap-5">{children}</section>
      </div>
    </main>
  );
}
