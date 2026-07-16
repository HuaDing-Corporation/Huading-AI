"use client";

import { useCallback, useState } from "react";
import { ChevronLeft, Clapperboard, Eraser, ImagePlus, Mic, PenLine, ScanSearch, Share2, ShieldCheck, Store, UserRound, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { EcomVideoForm } from "@/components/workbench/ecom-video-form";
import { VideoGenForm } from "@/components/workbench/video-gen-form";
import { NewVideoForm } from "@/components/workbench/new-video-form";
import { PhotoImageForm } from "@/components/workbench/photo-image-form";
import { CopywritingForm } from "@/components/workbench/copywriting-form";
import { EcomImageWorkbench } from "@/components/workbench/ecom-image-workbench";
import { ReversePromptForm } from "@/components/workbench/reverse-prompt-form";
import { GenerationHistory } from "@/components/tasks/generation-history";
import { Sidebar } from "@/components/layout/sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { TopBar } from "@/components/layout/top-bar";
import type { WorkbenchPrefill } from "@/lib/api/reverse-prompt";
import { copy } from "@/lib/copy";

type WorkbenchMode = "avatar_talk" | "seedance_i2v" | "video_gen" | "photo" | "copywriting" | "ecom_image" | "reverse_prompt";
type VideoMode = "avatar_talk" | "seedance_i2v";

// Workbench modes 顺序（WORKBENCH-TAB-ORDER-0001，2026-07-10 用户指定）：
// 数字人口播 / 提示词反推 / 图片生成·修改 / 电商图 / 文案仿写 / 电商带货 i2v / 视频生成 i2v。仅重排，id/label/图标/逻辑不变。
const MODES: { id: WorkbenchMode; label: string; Icon: LucideIcon }[] = [
  { id: "avatar_talk", label: copy.workbench.modeAvatar, Icon: UserRound },
  { id: "reverse_prompt", label: copy.workbench.modeReverse, Icon: ScanSearch },
  { id: "photo", label: copy.workbench.modePhoto, Icon: ImagePlus },
  { id: "ecom_image", label: copy.workbench.modeEcomImage, Icon: Eraser },
  { id: "copywriting", label: copy.workbench.modeCopywriting, Icon: PenLine },
  { id: "seedance_i2v", label: copy.workbench.modeEcom, Icon: Store },
  { id: "video_gen", label: copy.workbench.modeVideoGen, Icon: Clapperboard }
];
const DEFAULT_MODE: WorkbenchMode = "avatar_talk";

export default function Home() {
  const router = useRouter();
  const [mode, setMode] = useState<WorkbenchMode>(DEFAULT_MODE);
  // WORKBENCH-KEEPALIVE-UI-0001 · 惰性挂载 + 挂载后常驻：旧的条件渲染切 mode 即卸载表单 = React state 全销毁
  // （提示词/上传图/参数全丢，用户实报痛点）。改为：某 mode 首次被访问才入列挂载（首屏只挂默认 mode，不把 7 个
  // 表单 mount 时的 GET 全量打出），此后仅隐藏、不卸载 → 输入保活。
  const [mounted, setMounted] = useState<WorkbenchMode[]>([DEFAULT_MODE]);
  const activate = useCallback((next: WorkbenchMode) => {
    setMounted((prev) => (prev.includes(next) ? prev : [...prev, next]));
    setMode(next);
  }, []);

  // 一次性 prefill：文案模式「用此文案」(script-only) 与 提示词反推「带入」(富载荷) 共用同一缓冲。
  const [pendingPrefill, setPendingPrefill] = useState<WorkbenchPrefill | null>(null);
  // 🔴 prefill 消费时机（本次改造的要害）：改造前 5 个目标表单都是 `useState(() => initialX ?? "")` 惰性初始化 +
  // prefillConsumed ref 闩锁 —— 注入只发生在 mount 那一刻，全靠「切 mode 必然重挂」才生效。面板常驻后表单不再
  // 重挂：props 变了但 initializer 不再执行（值注不进去），而 consumed effect 仍照常触发 → clearPrefill 把载荷当
  // 「已消费」丢弃 → 用户看不到值也无从重试（静默吞噬）；ref 闩锁还会永不复位 → 缓冲此后再也清不掉。
  // 解法（§二 授权的「重新设计消费时机」）：各目标表单改为 useEffect 同步 props —— 只写 prefill 真正带来的字段，
  // 用户已填的其它输入原样保留。（另一条路「给目标表单换 key 强制重挂」已否决：带入会连带清空用户刚填的
  // topic / 音色 / 已传的形象图，等于用一个 remount 换另一个 remount，与本包目标自相矛盾。）
  const injectPrefill = useCallback(
    (prefill: WorkbenchPrefill) => {
      setPendingPrefill(prefill);
      activate(prefill.target);
    },
    [activate]
  );
  const useCopyInVideo = (target: VideoMode, script: string) => injectPrefill({ target, script });
  // 提示词反推「带入 X」：BE 载荷经 fillTargetToPrefill 已落好字段，这里切模式 + 缓冲，目标表单 effect 同步消费。
  const applyPrefill = (prefill: WorkbenchPrefill) => injectPrefill(prefill);
  // 🔴 必须 useCallback 固定引用：目标表单的 prefill effect 把它列进依赖数组。旧代码每次渲染换引用（effect 每渲染
  // 重跑，全靠那个永不复位的 ref 闩锁挡住重复回调）；闩锁已随消费改造删除，改由「clearPrefill 后 props 回落
  // undefined → effect early-return」防重入，故这里的引用必须稳定。
  const clearPrefill = useCallback(() => setPendingPrefill(null), []);

  const onBack = () => {
    if (typeof window !== "undefined" && window.history.length > 1) router.back();
    else router.push("/");
  };

  // 各 mode 的表单（props 与改造前逐字一致：pendingPrefill 判别式收窄 + onPrefillConsumed 回调）。
  const renderForm = (m: WorkbenchMode) => {
    switch (m) {
      case "avatar_talk":
        return (
          <NewVideoForm
            initialTopic={pendingPrefill?.target === "avatar_talk" ? pendingPrefill.topic : undefined}
            initialScript={pendingPrefill?.target === "avatar_talk" ? pendingPrefill.script : undefined}
            onPrefillConsumed={clearPrefill}
          />
        );
      case "seedance_i2v":
        return (
          <EcomVideoForm
            initialTopic={pendingPrefill?.target === "seedance_i2v" ? pendingPrefill.topic : undefined}
            initialScenePrompt={pendingPrefill?.target === "seedance_i2v" ? pendingPrefill.scenePrompt : undefined}
            initialScript={pendingPrefill?.target === "seedance_i2v" ? pendingPrefill.script : undefined}
            onPrefillConsumed={clearPrefill}
          />
        );
      case "video_gen":
        return (
          <VideoGenForm
            initialPrompt={pendingPrefill?.target === "video_gen" ? pendingPrefill.prompt : undefined}
            onPrefillConsumed={clearPrefill}
          />
        );
      case "reverse_prompt":
        return <ReversePromptForm onApplyPrefill={applyPrefill} />;
      case "copywriting":
        return <CopywritingForm onUseInVideo={useCopyInVideo} />;
      case "ecom_image":
        return (
          <EcomImageWorkbench
            initialTool={pendingPrefill?.target === "ecom_image" ? pendingPrefill.tool : undefined}
            initialCustom={pendingPrefill?.target === "ecom_image" ? pendingPrefill.custom : undefined}
            onPrefillConsumed={clearPrefill}
          />
        );
      case "photo":
        return (
          <PhotoImageForm
            initialPrompt={pendingPrefill?.target === "photo" ? pendingPrefill.prompt : undefined}
            onPrefillConsumed={clearPrefill}
          />
        );
    }
  };

  return (
    <main className="min-h-screen p-5 md:p-7">
      <div className="mx-auto grid max-w-[1600px] grid-cols-1 grid-rows-[auto_1fr] gap-5 md:grid-cols-[248px_minmax(0,1fr)]">
        <TopBar />
        <Sidebar />

        <section className="flex min-w-0 flex-col gap-5">
          {/* B2: breadcrumb / back navigation；flex-wrap 使多入口在窄屏换行不溢出 */}
          <nav className="flex flex-wrap items-center gap-1.5 px-1 text-[12.5px]" aria-label="面包屑">
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <ChevronLeft size={15} strokeWidth={2} /> 返回
            </button>
            <span className="text-ink-faint">/</span>
            <span className="text-ink-soft">工作台</span>
            <Link
              href="/brand-voices"
              className="ml-auto inline-flex items-center gap-1 rounded-field px-2 py-1 text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <Mic size={14} strokeWidth={2} /> {copy.brandVoice.entry}
            </Link>
            <Link
              href="/label-settings"
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <ShieldCheck size={14} strokeWidth={2} /> {copy.label.entry}
            </Link>
            <Link
              href="/publish"
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <Share2 size={14} strokeWidth={2} /> {copy.publish.pageTitle}
            </Link>
          </nav>

          <header className="flex flex-wrap items-end gap-3.5 px-1">
            <h1 className="text-[27px] font-semibold tracking-[1px] text-ink">工作台</h1>
            <p className="mb-1 text-[13.5px] text-ink-soft">输入主题，一键生成成片</p>
            <div className="ml-auto flex min-w-0 items-center gap-2">
              {/* 生成模式切换：5 模式 chip。窄屏(~375/390px)横向滚动而非撑破页面，
                  桌面单行容纳→无滚动条、布局不变（FIX1 P1）。 */}
              <div
                role="group"
                aria-label={copy.workbench.modeGroupLabel}
                className="flex min-w-0 gap-1 overflow-x-auto rounded-pill border border-line-gold bg-glass-soft p-1"
              >
                {MODES.map(({ id, label, Icon }) => {
                  const active = mode === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => activate(id)}
                      aria-pressed={active}
                      className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-pill px-3 py-1.5 text-[12.5px] outline-none transition-colors focus-visible:shadow-focus-gold ${
                        active
                          ? "bg-chip-sel font-medium text-gold-deep"
                          : "text-ink-soft hover:bg-glass-hover"
                      }`}
                    >
                      <Icon size={14} strokeWidth={1.8} /> {label}
                    </button>
                  );
                })}
              </div>
              {(mode === "avatar_talk" || mode === "seedance_i2v") && (
                <span className="shrink-0 rounded-pill border border-line-gold bg-glass-soft px-3.5 py-2 text-[12.5px] text-ink-soft">
                  {copy.workbench.aspectBadge}
                </span>
              )}
            </div>
          </header>

          {/* Form column + task list; both widen on large screens, stack on narrow. */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(320px,400px)_minmax(0,1fr)]">
            {/* 常驻面板（KEEPALIVE）：隐藏一律用 HTML `hidden` **属性**（UA 样式 [hidden]{display:none}），而不是
                Tailwind 的 hidden class —— 只有 display:none 能同时做到「从可及性树移除 + Tab 键跳过 + 不占 grid 位」
                （读屏不会读到 7 份表单内容，焦点不会掉进看不见的表单）。隐藏态**不挂任何设 display 的 class**，
                否则 CSS 会盖掉 [hidden] 的 UA 样式；激活态用 display:contents 让 wrapper 透明 → 表单本体仍是 grid
                的直接子项，布局与改造前逐像素一致。key 恒为 mode（绝不可掺入会变的值：key 一变即重挂 = state 全丢，
                正是本包要消灭的行为）。 */}
            {mounted.map((m) => {
              const active = m === mode;
              return (
                <div
                  key={m}
                  data-testid={`panel-${m}`}
                  hidden={!active}
                  className={active ? "contents" : undefined}
                >
                  {renderForm(m)}
                </div>
              );
            })}
            <TaskList />
          </div>

          {/* 反推历史「带入生成」（HISTORY-VIDEO-REVERSE-UI-0001）复用同一条 prefill 闭环：
              传**稳定引用** injectPrefill（useCallback），不传每次渲染换引用的 applyPrefill —— 后者一旦进了
              下游的 effect 依赖数组，就会重蹈 #181 注释里「effect 每渲染重跑」的覆辙。 */}
          <GenerationHistory onApplyPrefill={injectPrefill} />
        </section>
      </div>
    </main>
  );
}
