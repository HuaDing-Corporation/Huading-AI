"use client";

import { useEffect, useState } from "react";

import { copy } from "@/lib/copy";
import { formatElapsed } from "@/lib/sse/elapsed";
import { cn } from "@/lib/utils";

/**
 * 「仍在生成（已 X 分 Y 秒）」——**两条心跳通道共用的同一份呈现**（GEN-HEARTBEAT-UI-0001 · FIX1）：
 *   ① 图片生成：SSE 事件带 heartbeat_at → TaskCard（见 tasks/generating-elapsed.tsx）
 *   ② 电商详情图：GET /ecom-images/replicate/{id} 轮询响应带 heartbeat_at → 详情图向导等待态
 * 抽在这里是为了「计时怎么走、文案怎么说」只有一处实现，两条路不会各自漂移。
 *
 * 🔴 **它是计时，不是进度**：不画进度条、不推百分比、不预估剩余时间。唯一在动的是真实流逝的秒数。
 * 🔴 `startedAt` 是**任务开始时刻**，不是收到第一个心跳的时刻——否则等了很久才来首个心跳的任务会显示
 *    「已 0 秒」，那是低报等待时间，属于不诚实的那一侧。
 * 🔴 本组件**从不解析 heartbeat_at 的值**：它在调用方只当"还活着"的布尔证据用。所以 BE 降级返回
 *    `heartbeat_at: null`、或给个怪格式，都不可能在这里变成「已 NaN 秒」/「Invalid Date」。
 *
 * a11y：**故意不做 aria-live**——每秒一次的读屏播报是噪音，这条不是需要抢播的告警；
 * 它随所在区域被读到即可（详情图那侧外层已是 role="status" aria-live="polite"，那是给"生成中"整体状态用的）。
 * `tabular-nums` 防止秒数进位时的宽度抖动。
 */
export function ElapsedSince({ startedAt, className }: { startedAt: number; className?: string }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    // 每秒重算一次；秒是这条文案的最小刻度，再密只是无谓渲染。
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <span className={cn("block text-[12px] tabular-nums text-ink-soft", className)}>
      {copy.tasks.stillGenerating(formatElapsed(now - startedAt))}
    </span>
  );
}
