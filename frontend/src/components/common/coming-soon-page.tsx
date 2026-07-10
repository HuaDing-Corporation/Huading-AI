"use client";

import { ChevronLeft, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { copy } from "@/lib/copy";

/**
 * 统一「即将上线」占位页（UI-COMINGSOON-TENANT-RENAME-0001）。外壳仿其它独立页；内容为友好空态
 * （图标 + 「该功能即将上线，敬请期待」+ 返回工作台）。title 为板块名（面包屑/标题显示）。
 */
export function ComingSoonPage({ title }: { title: string }) {
  const router = useRouter();
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
            <span className="text-ink-soft">
              {title}
              {copy.comingSoon.navSuffix}
            </span>
          </nav>

          <header className="px-1">
            <h1 className="text-[27px] font-semibold tracking-[1px] text-ink">
              {title}
              {copy.comingSoon.navSuffix}
            </h1>
          </header>

          <Card>
            <div className="flex flex-col items-center gap-3 py-16 text-center" role="status" aria-live="polite">
              <span className="flex h-14 w-14 items-center justify-center rounded-full bg-glass-soft text-gold-deep">
                <Sparkles size={26} strokeWidth={1.6} />
              </span>
              <p className="text-[15px] font-medium text-ink">{copy.comingSoon.title}</p>
              <p className="max-w-sm text-[13px] text-ink-soft">{copy.comingSoon.desc}</p>
              <Button variant="soft" size="sm" onClick={() => router.push("/")} className="mt-1">
                {copy.comingSoon.back}
              </Button>
            </div>
          </Card>
        </section>
      </div>
    </main>
  );
}
