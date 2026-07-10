"use client";

import { Suspense } from "react";
import { ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";

import { PublishCenter } from "@/components/publish/publish-center";
import { ComingSoonPage } from "@/components/common/coming-soon-page";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { isComingSoon } from "@/lib/coming-soon";
import { copy } from "@/lib/copy";

/** 发布中心页（PUBLISH-UI-0001）—— 独立路由 /publish。外壳仿工作台；PublishCenter 用
 *  useSearchParams 取产物来源，故包 Suspense(Next 静态渲染要求)。
 *  UI-COMINGSOON-TENANT-RENAME-0001：coming-soon gate 开启时旁路真内容显示占位页（真组件 PublishCenter 保留、可逆）。 */
export default function PublishPage() {
  const router = useRouter();

  // 「即将上线」占位 gate（可逆）：置 COMING_SOON.publish=false 即恢复下方真发布中心。
  if (isComingSoon("publish")) return <ComingSoonPage title={copy.publish.pageTitle} />;
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
            <span className="text-ink-soft">{copy.publish.pageTitle}</span>
          </nav>

          <header className="px-1">
            <h1 className="text-[27px] font-semibold tracking-[1px] text-ink">{copy.publish.pageTitle}</h1>
            <p className="mt-1 text-[13.5px] text-ink-soft">{copy.publish.pageSubtitle}</p>
          </header>

          <Suspense fallback={<p className="px-1 text-[13px] text-ink-soft">{copy.publish.recordsLoading}</p>}>
            <PublishCenter />
          </Suspense>
        </section>
      </div>
    </main>
  );
}
