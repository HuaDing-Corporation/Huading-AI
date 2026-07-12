"use client";

import { ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";

import { AnalyticsDashboard } from "@/components/analytics/analytics-dashboard";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { useAuth } from "@/lib/auth/auth-context";
import { canViewAnalytics, canViewPlatformAnalytics } from "@/lib/auth/vip";
import { copy } from "@/lib/copy";

/**
 * 数据看板页（ANALYTICS-UI-0001 / ADMIN-VIP-GATE-UI-0001）—— 路由 /analytics。侧边栏「数据看板」**始终显示**；
 * 门禁在 AnalyticsDashboard：非管理员且非 huading plan → 403 ANALYTICS_PLAN_REQUIRED → VIP 友好页。外壳仿工作台/批量。
 */
export default function AnalyticsPage() {
  const router = useRouter();
  const { session } = useAuth();
  // 副标题据 entitlement：平台方（analytics_platform）「全站」；VIP 客户（有 analytics_view 无 platform）「我的用量」；
  // 无权限（走友好页）沿用平台文案（不误导为个人视图）。
  const subtitle =
    canViewAnalytics(session) && !canViewPlatformAnalytics(session)
      ? copy.analytics.pageSubtitleOwn
      : copy.analytics.pageSubtitle;
  const onBack = () => {
    if (typeof window !== "undefined" && window.history.length > 1) router.back();
    else router.push("/");
  };

  return (
    <main className="min-h-screen p-5 md:p-7">
      <div className="mx-auto grid max-w-[1600px] grid-cols-1 grid-rows-[auto_1fr] gap-5 md:grid-cols-[248px_minmax(0,1fr)]">
        <TopBar />
        <Sidebar />

        <section className="flex min-w-0 flex-col gap-5">
          <nav className="flex items-center gap-1.5 px-1 text-[12.5px]" aria-label="面包屑">
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <ChevronLeft size={15} strokeWidth={2} /> 返回
            </button>
            <span className="text-ink-faint">/</span>
            <span className="text-ink-soft">{copy.analytics.pageTitle}</span>
          </nav>

          <header className="px-1">
            <h1 className="text-[27px] font-semibold tracking-[1px] text-ink">{copy.analytics.pageTitle}</h1>
            <p className="mt-1 text-[13.5px] text-ink-soft">{subtitle}</p>
          </header>

          <AnalyticsDashboard />
        </section>
      </div>
    </main>
  );
}
