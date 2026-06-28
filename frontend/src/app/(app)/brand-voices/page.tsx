"use client";

import { ChevronLeft } from "lucide-react";
import { useRouter } from "next/navigation";

import { BrandVoiceCreate } from "@/components/brand-voice/brand-voice-create";
import { BrandVoiceList } from "@/components/brand-voice/brand-voice-list";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { copy } from "@/lib/copy";

/** 品牌音色管理页（BRAND-VOICE-UI-0001）—— 独立路由 /brand-voices。外壳仿工作台(TopBar+Sidebar)。 */
export default function BrandVoicesPage() {
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
            <span className="text-ink-soft">{copy.brandVoice.pageTitle}</span>
          </nav>

          <header className="px-1">
            <h1 className="text-[27px] font-semibold tracking-[1px] text-ink">{copy.brandVoice.pageTitle}</h1>
            <p className="mt-1 text-[13.5px] text-ink-soft">{copy.brandVoice.pageSubtitle}</p>
          </header>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(320px,440px)_minmax(0,1fr)]">
            <BrandVoiceCreate />
            <BrandVoiceList />
          </div>
        </section>
      </div>
    </main>
  );
}
