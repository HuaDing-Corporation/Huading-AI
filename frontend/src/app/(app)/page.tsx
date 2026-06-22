"use client";

import { useState } from "react";
import { ChevronLeft, Store, UserRound, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { EcomVideoForm } from "@/components/workbench/ecom-video-form";
import { NewVideoForm } from "@/components/workbench/new-video-form";
import { Sidebar } from "@/components/layout/sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { TopBar } from "@/components/layout/top-bar";
import { copy } from "@/lib/copy";

type WorkbenchMode = "avatar_talk" | "seedance_i2v";

// Workbench generation modes. avatar_talk renders the existing NewVideoForm;
// seedance_i2v renders the 电商带货 product image-to-video form.
const MODES: { id: WorkbenchMode; label: string; Icon: LucideIcon }[] = [
  { id: "avatar_talk", label: copy.workbench.modeAvatar, Icon: UserRound },
  { id: "seedance_i2v", label: copy.workbench.modeEcom, Icon: Store }
];

export default function Home() {
  const router = useRouter();
  const [mode, setMode] = useState<WorkbenchMode>("avatar_talk");

  const onBack = () => {
    if (typeof window !== "undefined" && window.history.length > 1) router.back();
    else router.push("/");
  };

  return (
    <main className="min-h-screen p-5 md:p-7">
      <div className="mx-auto grid max-w-[1280px] grid-cols-1 grid-rows-[auto_1fr] gap-5 md:grid-cols-[248px_minmax(0,1fr)]">
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
            <div className="ml-auto flex items-center gap-2">
              {/* 生成模式切换：数字人口播 / 电商带货 */}
              <div
                role="group"
                aria-label={copy.workbench.modeGroupLabel}
                className="flex gap-1 rounded-pill border border-line-gold bg-glass-soft p-1"
              >
                {MODES.map(({ id, label, Icon }) => {
                  const active = mode === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setMode(id)}
                      aria-pressed={active}
                      className={`flex items-center gap-1.5 rounded-pill px-3 py-1.5 text-[12.5px] outline-none transition-colors focus-visible:shadow-focus-gold ${
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
              <span className="rounded-pill border border-line-gold bg-glass-soft px-3.5 py-2 text-[12.5px] text-ink-soft">
                {copy.workbench.aspectBadge}
              </span>
            </div>
          </header>

          {/* B1: form column ~320px (min 300), task list takes the rest; stacks on narrow */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(300px,340px)_minmax(0,1fr)]">
            {mode === "avatar_talk" ? <NewVideoForm /> : <EcomVideoForm />}
            <TaskList />
          </div>
        </section>
      </div>
    </main>
  );
}
