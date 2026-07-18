"use client";

import { LandingTopbar } from "@/components/landing/landing-topbar";
import {
  ContactSection,
  CtaSection,
  LandingFooter,
  LandingHero,
  ModuleGrid,
  SampleWall,
  StepsSection
} from "@/components/landing/landing-sections";
import { copy } from "@/lib/copy";

/**
 * 落地页 /landing (LANDING-ENTRY-UI-0001 · 暖金科技风长页)。公开路由：
 *  - 未登录进站根 `/` → (app)/layout 鉴权门 replace 到本页（访客入口）；
 *  - 已登录进站根 `/` → 直接是控制台（(app)/page 工作台，零回归）；
 *  - 已登录显式访问 /landing → 正常浏览，顶栏显示头像态（任务包「落地页顶栏两态都要」），
 *    一键「进控制台」回 `/`——不强制跳转，避免头像态成为死 UI。
 * 结构（冻结 §三）：顶栏 → Hero → 七模块 → 样片墙(占位) → 五步上手 → 注册 CTA → 页脚。
 */
export default function LandingPage() {
  return (
    <div className="min-h-dvh">
      <a
        href="#landing-main"
        className="sr-only rounded-field bg-glass-fill px-3 py-2 text-[13px] text-ink focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50"
      >
        {copy.landing.skipToMain}
      </a>
      <LandingTopbar />
      <main id="landing-main">
        <LandingHero />
        <ModuleGrid />
        <SampleWall />
        <StepsSection />
        <CtaSection />
        {/* LANDING-CONTACT-UI-0001：CTA 说「注册后联系我们开通额度」→ 下一屏就是联系方式 */}
        <ContactSection />
      </main>
      <LandingFooter />
    </div>
  );
}
