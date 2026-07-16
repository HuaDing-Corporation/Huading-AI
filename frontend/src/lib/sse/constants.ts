// Centralized, configurable SSE / progress / script constants.
// GEN-TIMEOUT-1500-UI-0001：前端看门狗必须**晚于后端权威超时**（BE 包把等待拉到 1500s，nginx SSE 1800s）——
// 否则前端在 120s/900s 就误杀了后端还在生成的任务（图片生成无逐轮进度回调，120s 内一个进度事件都收不到 → 必中）。
// 看门狗只作**后端失联的最后一道兜底**（worker 崩/SSE 断/任务卡死），不是生成超时的判定者，故两阈值都要 > 1500s。
export const STALL_MS = 1_620_000; // 无进度窗口 27min：> BE 权威 1500s + 120s 余量（后端失联兜底，非生成超时判定）
export const HARD_CAP_MS = 1_800_000; // 从 queued 起的绝对上限 30min：对齐 nginx SSE 1800s，> BE 1500s
export const POLL_MS = 2_000; // poll-fallback interval
export const MAX_SCRIPT_SECONDS = 60; // OmniHuman audio ≤60s
export const CPS = 5; // chinese chars/second estimate
export const estSeconds = (script: string, speed = 1) =>
  Math.min(MAX_SCRIPT_SECONDS, Math.ceil(script.length / CPS / (speed || 1)));
