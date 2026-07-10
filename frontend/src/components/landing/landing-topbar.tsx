"use client";

import Link from "next/link";

import { AuthEntry } from "@/components/landing/auth-entry";
import { LogoBadge } from "@/components/ui/logo";
import { copy } from "@/lib/copy";

const anchorClass =
  "rounded-pill px-3 py-1.5 text-[13px] text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold";

/**
 * 落地页 sticky 玻璃顶栏 (冻结 §三)：鼎徽 + 「华鼎AI · 短视频引擎」(--grad-gold 字标) ｜
 * 锚点 功能 · 样片 · 教学（≥sm 显示）｜ 右上角入口两态 (AuthEntry)。
 */
export function LandingTopbar() {
  return (
    <header className="sticky top-0 z-40 px-3 pt-3 sm:px-5">
      <div className="glass mx-auto flex max-w-6xl items-center gap-3 rounded-card px-4 py-3 sm:gap-5 sm:px-6">
        <Link
          href="/landing"
          aria-label={copy.landing.brand}
          className="flex items-center gap-3 rounded-mark outline-none focus-visible:shadow-focus-gold"
        >
          <LogoBadge className="h-10 w-10" />
          <b className="bg-grad-gold bg-clip-text text-[15.5px] font-semibold tracking-[.5px] text-transparent sm:text-[17px]">
            {copy.landing.brand}
          </b>
        </Link>

        <nav aria-label={copy.landing.navAria} className="ml-auto hidden items-center gap-1 sm:flex">
          <a href="#modules" className={anchorClass}>
            {copy.landing.navFeatures}
          </a>
          <a href="#samples" className={anchorClass}>
            {copy.landing.navSamples}
          </a>
          <a href="#steps" className={anchorClass}>
            {copy.landing.navSteps}
          </a>
        </nav>

        <div className="ml-auto sm:ml-2">
          <AuthEntry />
        </div>
      </div>
    </header>
  );
}
