"use client";

import { ElapsedSince } from "@/components/common/elapsed-since";
import type { TrackedTask } from "@/lib/sse/progress-mapping";

/**
 * 会话卡的诚实等待反馈（GEN-HEARTBEAT-UI-0001 · 通道①：SSE 事件带 heartbeat_at）。
 *
 * 显示门控 = **生成中且收到过心跳**（`heartbeatAt != null`，冻结 §四）——没有心跳就没有"还活着"的证据，
 * 那时候多说一个字都是替后端担保。计时与文案由 ElapsedSince 统一承担（与详情图那条通道同一份实现）。
 *
 * 🔴 心跳期间百分比是**冻结**的（BE 心跳不改 progress），这里绝不去动它：没有假进度条、没有自己爬的
 * 百分比、没有「还需 X 分钟」的预估。
 */
export function GeneratingElapsed({ task }: { task: TrackedTask }) {
  // 逐条 guard（不合并成一个布尔 const）：这样 TS 能把 startedAt 收窄成 number，省掉 `as number` 强转——
  // 同仓踩过的坑就在隔壁（progress-mapping.ts 那行 `as Partial<VideoDetail>` 把类型检查变成了摆设）。
  const generating = task.status === "running" || task.status === "queued";
  if (!generating || task.heartbeatAt == null || task.startedAt == null) return null;
  return <ElapsedSince startedAt={task.startedAt} className="mt-0.5" />;
}
