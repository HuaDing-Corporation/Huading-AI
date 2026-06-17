"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, Store } from "lucide-react";

import { NewVideoCard } from "@/components/workbench/new-video-card";
import { Sidebar } from "@/components/layout/sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { TopBar } from "@/components/layout/top-bar";
import { useAuth } from "@/lib/auth/auth-context";

export default function Home() {
  const { session, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  // Avoid rendering the console (and firing authed requests) until we know.
  if (!ready || !session) return <main className="min-h-screen" aria-busy="true" />;

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
            <div className="ml-auto flex gap-2">
              <span className="flex items-center gap-1.5 rounded-pill border border-line-gold bg-glass-soft px-3.5 py-2 text-[12.5px] text-ink-soft">
                <Store size={14} strokeWidth={1.8} /> 电商带货
              </span>
              <span className="rounded-pill border border-line-gold bg-glass-soft px-3.5 py-2 text-[12.5px] text-ink-soft">
                竖屏 9:16
              </span>
            </div>
          </header>

          {/* B1: form column ~320px (min 300), task list takes the rest; stacks on narrow */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(300px,340px)_minmax(0,1fr)]">
            <NewVideoCard />
            <TaskList />
          </div>
        </section>
      </div>
    </main>
  );
}
