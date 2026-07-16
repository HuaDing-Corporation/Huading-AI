"use client";

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "huading.label_toggle_enabled";

// 存储不可用（隐私模式 / 禁用存储）时的内存态承载；localStorage 正常时不参与。
let memoryFallback: boolean | null = null;

/** 读取记忆值；SSR / 无存储 / 异常 → 内存态或默认关(false)。 */
function readStored(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return memoryFallback ?? false;
  }
}

function writeStored(value: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    memoryFallback = value; // 隐私模式/存储禁用：仅内存态，忽略持久化失败
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
