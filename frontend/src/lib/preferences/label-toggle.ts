"use client";

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "huading.label_toggle_enabled";

// 存储**写不进**时的内存态承载（隐私模式 / quota 满 / 只读模式）；localStorage 可写时恒为 null、不参与。
// 🔴 FIX1：它必须是**权威覆盖层**而非 catch 兜底。旧写法把它只放在 readStored 的 catch 里，而 setItem 抛、
// getItem 正常是最典型的形态（隐私模式/quota 满）—— 那样「写进内存、读却走 localStorage 拿旧值」，两个分支
// 永不相交 → 开关点了不动、还提交错值。根因是本次改造把权威从 React state 迁到了 readStored()：迁移前写失败
// 只丢持久化（state 兜着 UI 照常翻转），迁移后写失败直接升级成功能失效。故内存态必须先于 localStorage 被读到。
let memoryFallback: boolean | null = null;

/** 读取记忆值；SSR → false。内存态存在（= 上次写没写进去）时它是权威；否则读 localStorage，读失败 → 默认关。 */
function readStored(): boolean {
  if (typeof window === "undefined") return false;
  if (memoryFallback !== null) return memoryFallback;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeStored(value: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
    // 写成功 → 存储可用，localStorage 重新是权威，内存兜底立即退位。
    // 不清会让一次偶发失败（如 quota 满）后的陈旧内存态**永久压过** localStorage，连另一标签页的 storage
    // 事件也被吃掉 → 跨标签页同步坏死。清除是安全的：写成功即证明存储可用，无需再兜底。
    memoryFallback = null;
  } catch {
    memoryFallback = value; // 隐私模式/存储禁用/quota 满：仅内存态，忽略持久化失败，但 UI 必须照常翻转
  }
}

// WORKBENCH-KEEPALIVE-UI-0001：六大面板改为「挂载后常驻」后，旧实现的 `useEffect(() => setEnabled(readStored()), [])`
// （mount 时读一次 localStorage）不再随切 tab 重跑 —— 在 A 面板改了开关，已挂载的 B 面板会一直显示旧值、并按这个
// 陈旧值提交 apply_visible_label。故改为外部 store 订阅：所有实例共享同一快照，任一处 set 即广播 → 跨面板恒一致。
// （旧实现的「跨面板同步」其实是切 tab 重挂重读 localStorage 的副作用，常驻后必须显式订阅才能维持该语义。）
const listeners = new Set<() => void>();

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  window.addEventListener("storage", onStoreChange); // 另一标签页改同一 key
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

/** 首帧 / SSR 快照恒为关，保持「首帧渲染关、无 hydration mismatch」的原语义。 */
const getServerSnapshot = (): boolean => false;

/**
 * AI 生成标识开关的用户偏好（LABEL-TOGGLE-UI-0001）。跨六大面板共享同一 localStorage key →
 * 「记住上次选择，下次进入沿用」；**默认初值仍为关**。首帧渲染关（避免 SSR/hydration 不一致），
 * hydration 后应用记忆值。setter 同步写回 localStorage 并广播给所有已挂载实例。
 */
export function useLabelTogglePreference(): [boolean, (value: boolean) => void] {
  const enabled = useSyncExternalStore(subscribe, readStored, getServerSnapshot);

  const set = useCallback((value: boolean) => {
    if (typeof window === "undefined") return;
    writeStored(value);
    listeners.forEach((listener) => listener()); // 广播 → 所有面板重读快照
  }, []);

  return [enabled, set];
}
