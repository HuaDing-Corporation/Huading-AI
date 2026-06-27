"use client";

import { useState } from "react";
import { ChevronLeft, Eraser, ImagePlus, PenLine, Store, UserRound, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { EcomVideoForm } from "@/components/workbench/ecom-video-form";
import { NewVideoForm } from "@/components/workbench/new-video-form";
import { PhotoImageForm } from "@/components/workbench/photo-image-form";
import { CopywritingForm } from "@/components/workbench/copywriting-form";
import { EcomImageCutoutForm } from "@/components/workbench/ecom-image-cutout-form";
import { GenerationHistory } from "@/components/tasks/generation-history";
import { Sidebar } from "@/components/layout/sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { TopBar } from "@/components/layout/top-bar";
import { copy } from "@/lib/copy";

type WorkbenchMode = "avatar_talk" | "seedance_i2v" | "photo" | "copywriting" | "ecom_image";
type VideoMode = "avatar_talk" | "seedance_i2v";

// Workbench modes: 数字人口播 (video) / 电商带货 i2v (video) / 照片·AI 图 (image) / 文案仿写 (text) / 电商图·白底图 (image).
const MODES: { id: WorkbenchMode; label: string; Icon: LucideIcon }[] = [
  { id: "avatar_talk", label: copy.workbench.modeAvatar, Icon: UserRound },
  { id: "seedance_i2v", label: copy.workbench.modeEcom, Icon: Store },
  { id: "photo", label: copy.workbench.modePhoto, Icon: ImagePlus },
  { id: "copywriting", label: copy.workbench.modeCopywriting, Icon: PenLine },
  { id: "ecom_image", label: copy.workbench.modeEcomImage, Icon: Eraser }
];

export default function Home() {
  const router = useRouter();
  const [mode, setMode] = useState<WorkbenchMode>("avatar_talk");
  // 一次性 prefill：文案模式「用此文案」→ 注入目标视频表单的 script，目标表单 mount 消费后
  // 回调 clearPrefill 清空，避免口播↔电商来回切 remount 时重复注入旧文案。
  const [pendingPrefill, setPendingPrefill] = useState<{ target: VideoMode; script: string } | null>(null);
  const useCopyInVideo = (target: VideoMode, script: string) => {
    setPendingPrefill({ target, script });
    setMode(target);
  };
  const clearPrefill = () => setPendingPrefill(null);

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
          {/* B2: breadcrumb / back navigation */}
          <nav className="flex items-center gap-1.5 px-1 text-[12.5px]" aria-label="面包屑">
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1 rounded-field px-2 py-1 text-gold-deep outline-none transition-colors hover:bg-glass-soft focus-visible:shadow-focus-gold"
            >
              <ChevronLeft size={15} strokeWidth={2} /> 返回
            </button>
            <span className="text-ink-faint">/</span>
            <span className="text-ink-soft">工作台</span>
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
                      onClick={() => setMode(id)}
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
            {mode === "avatar_talk" ? (
              <NewVideoForm
                initialScript={pendingPrefill?.target === "avatar_talk" ? pendingPrefill.script : undefined}
                onPrefillConsumed={clearPrefill}
              />
            ) : mode === "seedance_i2v" ? (
              <EcomVideoForm
                initialScript={pendingPrefill?.target === "seedance_i2v" ? pendingPrefill.script : undefined}
                onPrefillConsumed={clearPrefill}
              />
            ) : mode === "copywriting" ? (
              <CopywritingForm onUseInVideo={useCopyInVideo} />
            ) : mode === "ecom_image" ? (
              <EcomImageCutoutForm />
            ) : (
              <PhotoImageForm />
            )}
            <TaskList />
          </div>

          <GenerationHistory />
        </section>
      </div>
    </main>
  );
}
