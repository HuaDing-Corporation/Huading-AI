import { cn } from "@/lib/utils";

// 落地页·科技动效装饰层 (LANDING-ENTRY-UI-0001)：漂浮金色粒子 + 呼吸光晕 + 暖色网格光栅。
// 纯装饰：aria-hidden + pointer-events-none，动画类见 globals.css（prefers-reduced-motion 全降级）。
// 粒子位置/节奏为**确定性常量**（非随机）：SSR/CSR 一致，零 hydration 抖动。

interface Particle {
  left: string;
  top: string;
  size: number;
  dur: number;
  delay: number;
  champ?: boolean; // true=香槟色，false=品牌金
}

const PARTICLES: Particle[] = [
  { left: "6%", top: "24%", size: 4, dur: 11, delay: 0 },
  { left: "14%", top: "62%", size: 3, dur: 14, delay: 1.2, champ: true },
  { left: "22%", top: "18%", size: 5, dur: 12, delay: 2.4 },
  { left: "32%", top: "74%", size: 3, dur: 16, delay: 0.8, champ: true },
  { left: "44%", top: "12%", size: 4, dur: 13, delay: 3.1 },
  { left: "52%", top: "66%", size: 2, dur: 18, delay: 1.6, champ: true },
  { left: "61%", top: "28%", size: 5, dur: 12, delay: 0.4 },
  { left: "70%", top: "70%", size: 3, dur: 15, delay: 2.8, champ: true },
  { left: "78%", top: "16%", size: 4, dur: 14, delay: 1.9 },
  { left: "86%", top: "56%", size: 3, dur: 17, delay: 0.6, champ: true },
  { left: "93%", top: "34%", size: 4, dur: 13, delay: 2.2 },
  { left: "38%", top: "42%", size: 2, dur: 19, delay: 3.6, champ: true }
];

/** 覆盖父容器（父需 relative + overflow-hidden）的装饰背景层，供 Hero / 注册 CTA 区复用。 */
export function LandingFx({ grid = false, className }: { grid?: boolean; className?: string }) {
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 z-0", className)}>
      {/* 暖色网格光栅（静态，中心渐隐） */}
      {grid && <div className="landing-grid-bg absolute inset-0" />}

      {/* 呼吸光晕球 ×2（champ / gold，radial 渐隐 + blur） */}
      <div
        className="landing-breathe absolute -left-24 top-8 h-[340px] w-[340px] rounded-full blur-3xl"
        style={{ background: "radial-gradient(closest-side, var(--champ), transparent)", opacity: 0.55 }}
      />
      <div
        className="landing-breathe absolute -right-20 bottom-0 h-[300px] w-[300px] rounded-full blur-3xl"
        style={{ background: "radial-gradient(closest-side, var(--gold), transparent)", opacity: 0.35, ["--breathe-delay" as string]: "2.5s" }}
      />

      {/* 漂浮金色粒子（≤14 个，transform-only 错峰浮动） */}
      {PARTICLES.map((p, i) => (
        <span
          key={i}
          className="landing-float absolute rounded-full"
          style={{
            left: p.left,
            top: p.top,
            width: p.size,
            height: p.size,
            background: p.champ ? "var(--champ)" : "var(--gold)",
            opacity: p.champ ? 0.5 : 0.3,
            ["--float-dur" as string]: `${p.dur}s`,
            ["--float-delay" as string]: `${p.delay}s`
          }}
        />
      ))}
    </div>
  );
}
