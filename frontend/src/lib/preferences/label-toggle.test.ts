import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useLabelTogglePreference } from "./label-toggle";

describe("useLabelTogglePreference (AI 标识开关记忆)", () => {
  afterEach(() => window.localStorage.clear());

  it("默认关：无存储 → false", () => {
    const { result } = renderHook(() => useLabelTogglePreference());
    expect(result.current[0]).toBe(false);
  });

  it("set(true) → 内存态 true + 写回 localStorage", () => {
    const { result } = renderHook(() => useLabelTogglePreference());
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
    expect(window.localStorage.getItem("huading.label_toggle_enabled")).toBe("1");
  });

  it("记忆：已存 1 → 新实例 mount 后读回 true（下次进入沿用）", () => {
    window.localStorage.setItem("huading.label_toggle_enabled", "1");
    const { result } = renderHook(() => useLabelTogglePreference());
    expect(result.current[0]).toBe(true); // useEffect 已 flush 应用记忆
  });

  it("set(false) → 写回 0", () => {
    window.localStorage.setItem("huading.label_toggle_enabled", "1");
    const { result } = renderHook(() => useLabelTogglePreference());
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(window.localStorage.getItem("huading.label_toggle_enabled")).toBe("0");
  });
});
