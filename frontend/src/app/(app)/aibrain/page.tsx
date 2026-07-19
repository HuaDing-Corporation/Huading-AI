"use client";

// 华鼎AI智脑 · 页面（AIBRAIN-UI-0001）。本项目无共享 shell —— 每个 (app) 页自渲 TopBar + Sidebar（参 coming-soon-page）。
// 未登录由 (app)/layout.tsx 统一 gate（跳 /landing）。

import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { AibrainChat } from "@/components/aibrain/aibrain-chat";

export default function AibrainPage() {
  return (
    <main className="min-h-screen p-5 md:p-7">
      <div className="mx-auto grid max-w-[1600px] grid-cols-1 grid-rows-[auto_1fr] gap-5 md:grid-cols-[248px_minmax(0,1fr)]">
        <TopBar />
        <Sidebar />
        <AibrainChat />
      </div>
    </main>
  );
}
