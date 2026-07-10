import Link from "next/link";
import {
  Clapperboard,
  Eraser,
  ImagePlus,
  PenLine,
  Play,
  Rocket,
  ScanSearch,
  Sparkles,
  Store,
  UserRound,
  type LucideIcon
} from "lucide-react";

import { LandingFx } from "@/components/landing/landing-fx";
import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";

// 落地页六大内容区块 (LANDING-ENTRY-UI-0001)：Hero / 七模块 / 样片墙(占位) / 五步上手 / 注册 CTA / 页脚。
// 全部静态展示（无 hooks/fetch）；文案取 copy.landing（冻结 §三 一字不差）；图标沿用工作台 lucide 映射。

const sectionTitleClass = "text-center text-[clamp(24px,3.4vw,34px)] font-bold text-ink";
const sectionSubClass = "mx-auto mt-3 max-w-xl text-center text-[15px] leading-[1.65] text-ink-soft";

/** Hero：徽标 + 流光主标题 + 副标 + 双 CTA + 数据条（背景叠粒子/光晕/网格装饰层）。 */
export function LandingHero() {
  const L = copy.landing;
  return (
    <section className="relative overflow-hidden px-5 pb-20 pt-14 sm:pb-24 sm:pt-20">
      <LandingFx grid />
      <div className="relative z-10 mx-auto max-w-3xl text-center">
        <span className="inline-flex items-center gap-1.5 rounded-pill border border-line-gold bg-glass-soft px-3.5 py-1.5 text-[12.5px] font-medium tracking-[.14em] text-gold-deep">
          <Sparkles size={13} strokeWidth={1.8} aria-hidden />
          {L.heroBadge}
        </span>
        <h1 className="landing-flow-text mt-6 text-[clamp(34px,6vw,58px)] font-bold leading-[1.16]">{L.heroTitle}</h1>
        <p className="mx-auto mt-5 max-w-xl text-[15px] leading-[1.65] text-ink-soft sm:text-base">{L.heroSub}</p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button asChild size="lg">
            {/* /register 由 AUTH-UI-0001 建：先占位链接；prefetch=false 免 404 预取噪音 */}
            <Link href="/register" prefetch={false}>
              {L.ctaRegister}
            </Link>
          </Button>
          <Button asChild variant="soft" size="lg">
            <Link href="/login">{L.ctaLogin}</Link>
          </Button>
        </div>
        {/* 数据条（冻结 §三※：可信表述，不用「500+」） */}
        <p className="mt-8 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[14.5px] font-medium text-ink-soft">
          <span>{L.statModules}</span>
          <span aria-hidden className="text-gold">
            ·
          </span>
          <span>{L.statAuto}</span>
          <span aria-hidden className="text-gold">
            ·
          </span>
          <span>{L.statDistribute}</span>
        </p>
      </div>
    </section>
  );
}

const MODULES: { Icon: LucideIcon; title: string; desc: string }[] = [
  { Icon: UserRound, title: copy.landing.modAvatar, desc: copy.landing.modAvatarDesc },
  { Icon: Clapperboard, title: copy.landing.modVideoGen, desc: copy.landing.modVideoGenDesc },
  { Icon: Eraser, title: copy.landing.modEcomImage, desc: copy.landing.modEcomImageDesc },
  { Icon: ImagePlus, title: copy.landing.modPhoto, desc: copy.landing.modPhotoDesc },
  { Icon: PenLine, title: copy.landing.modCopywriting, desc: copy.landing.modCopywritingDesc },
  { Icon: Store, title: copy.landing.modEcomVideo, desc: copy.landing.modEcomVideoDesc },
  { Icon: ScanSearch, title: copy.landing.modReverse, desc: copy.landing.modReverseDesc }
];

/** 七大生产模块：玻璃卡 + 金描边 + 悬浮微动；第 8 位「更多能力持续上线」。 */
export function ModuleGrid() {
  const L = copy.landing;
  const cardClass =
    "glass rounded-card p-5 transition-[transform,box-shadow] duration-200 hover:-translate-y-[3px]";
  return (
    <section id="modules" className="scroll-mt-24 px-5 py-16 sm:py-20">
      <div className="mx-auto max-w-6xl">
        <h2 className={sectionTitleClass}>{L.modulesTitle}</h2>
        <p className={sectionSubClass}>{L.modulesSub}</p>
        <ul className="mt-10 grid list-none gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {MODULES.map(({ Icon, title, desc }) => (
            <li key={title} className={cardClass}>
              <span className="flex h-11 w-11 items-center justify-center rounded-mark border border-line-glass bg-grad-mark shadow-mark">
                <Icon size={20} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
              </span>
              <h3 className="mt-4 text-[16.5px] font-semibold text-ink">{title}</h3>
              <p className="mt-1.5 text-[13.5px] leading-[1.6] text-ink-soft">{desc}</p>
            </li>
          ))}
          <li className={`${cardClass} border border-dashed border-line-gold`}>
            <span className="flex h-11 w-11 items-center justify-center rounded-mark border border-line-glass bg-grad-mark shadow-mark">
              <Rocket size={20} strokeWidth={1.8} className="text-gold-deep" aria-hidden />
            </span>
            <h3 className="mt-4 text-[16.5px] font-semibold text-ink">{L.modMore}</h3>
          </li>
        </ul>
      </div>
    </section>
  );
}

// TODO(样片): 占位墙——待用户在后台生成真实样片后替换。预留接口位：把 src 填为真实样片
// 封面/视频地址即可（{ label, src?: string }），当前 src 均空 → 渲染金调微光占位块。
const SAMPLES: { label: string; src?: string }[] = [
  { label: copy.landing.sampleAvatar },
  { label: copy.landing.sampleEcomImage },
  { label: copy.landing.sampleEcomVideo },
  { label: copy.landing.sampleAiImage }
];

/** 效果样片墙（4 占位位，金调微光呼吸）。 */
export function SampleWall() {
  const L = copy.landing;
  return (
    <section id="samples" className="scroll-mt-24 px-5 py-16 sm:py-20">
      <div className="mx-auto max-w-6xl">
        <h2 className={sectionTitleClass}>{L.samplesTitle}</h2>
        <p className={sectionSubClass}>{L.samplesSub}</p>
        <ul className="mt-10 grid list-none gap-4 grid-cols-2 lg:grid-cols-4">
          {SAMPLES.map(({ label }) => (
            <li key={label} className="glass relative overflow-hidden rounded-card">
              <div className="relative flex aspect-[3/4] flex-col items-center justify-center gap-3">
                {/* 金调微光呼吸（装饰） */}
                <div
                  aria-hidden
                  className="landing-shimmer pointer-events-none absolute inset-0"
                  style={{ background: "radial-gradient(60% 46% at 50% 42%, var(--champ), transparent)" }}
                />
                <span className="relative flex h-12 w-12 items-center justify-center rounded-full border border-line-gold bg-glass-soft text-gold-deep">
                  <Play size={18} strokeWidth={1.8} aria-hidden />
                </span>
                <span className="relative text-[14px] font-medium text-ink">{label}</span>
                <span className="relative text-[11.5px] tracking-[.1em] text-ink-faint">{L.samplePlaceholder}</span>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

const STEPS = [copy.landing.step1, copy.landing.step2, copy.landing.step3, copy.landing.step4, copy.landing.step5];

/** 五步上手：金圆标序号 + 描述。 */
export function StepsSection() {
  const L = copy.landing;
  return (
    <section id="steps" className="scroll-mt-24 px-5 py-16 sm:py-20">
      <div className="mx-auto max-w-4xl">
        <h2 className={sectionTitleClass}>{L.stepsTitle}</h2>
        <ol className="mx-auto mt-10 flex max-w-2xl list-none flex-col gap-3">
          {STEPS.map((step, i) => (
            <li key={step} className="glass flex items-center gap-4 rounded-panel px-5 py-4">
              <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-grad-gold text-[14px] font-semibold text-ink shadow-avatar">
                {i + 1}
              </span>
              <span className="text-[14.5px] leading-[1.6] text-ink">{step}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/** 注册 CTA 区：中心金色光爆 + 发光主按钮。 */
export function CtaSection() {
  const L = copy.landing;
  return (
    <section className="relative overflow-hidden px-5 py-20 sm:py-24">
      <LandingFx />
      {/* 中心金色光爆（大半径 champ 径向） */}
      <div
        aria-hidden
        className="landing-breathe pointer-events-none absolute left-1/2 top-1/2 h-[420px] w-[640px] max-w-full -translate-x-1/2 -translate-y-1/2 rounded-full blur-3xl"
        style={{ background: "radial-gradient(closest-side, var(--champ), transparent)", opacity: 0.6 }}
      />
      <div className="relative z-10 mx-auto max-w-2xl text-center">
        <h2 className={sectionTitleClass}>{L.ctaTitle}</h2>
        <p className={sectionSubClass}>{L.ctaSub}</p>
        <Button asChild size="lg" className="mt-8">
          <Link href="/register" prefetch={false}>
            {L.ctaRegister}
          </Link>
        </Button>
      </div>
    </section>
  );
}

/** 页脚：© + 备案占位 + 链接占位（关于/联系/服务条款，AUTH 后续接真实页）。 */
export function LandingFooter() {
  const L = copy.landing;
  return (
    <footer className="border-t border-line-gold/40 px-5 py-8">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 text-[12.5px] text-ink-faint sm:flex-row">
        <span>{L.footerCopyright}</span>
        <span>{L.footerIcp}</span>
        <span className="flex items-center gap-3">
          <span>{L.footerAbout}</span>
          <span aria-hidden>·</span>
          <span>{L.footerContact}</span>
          <span aria-hidden>·</span>
          <span>{L.footerTerms}</span>
        </span>
      </div>
    </footer>
  );
}
