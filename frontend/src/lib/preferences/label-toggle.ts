"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "huading.label_toggle_enabled";

/** 读取记忆值；SSR / 无存储 / 异常 → 默认关(false)。 */
function readStored(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * AI 生成标识开关的用户偏好（LABEL-TOGGLE-UI-0001）。跨六大面板共享同一 localStorage key →
 * 「记住上次选择，下次进入沿用」；**默认初值仍为关**。首帧渲染关（避免 SSR/hydration 不一致），
 * mount 后再应用记忆值。setter 同步写回 localStorage。
 */
export function useLabelTogglePreference(): [boolean, (value: boolean) => void] {
  const [enabled, setEnabled] = useState(false); // 首帧默认关，无 hydration mismatch

  useEffect(() => {
    setEnabled(readStored()); // mount 后应用记忆
  }, []);

  const set = useCallback((value: boolean) => {
    setEnabled(value);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
      } catch {
        /* 隐私模式/存储禁用：仅内存态，忽略持久化失败 */
      }
    }
  }, []);

  return [enabled, set];
}
