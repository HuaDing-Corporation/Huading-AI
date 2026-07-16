import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "huading.label_toggle_enabled";

// WORKBENCH-KEEPALIVE-UI-0001 · FIX1：memoryFallback 是**模块级**状态（localStorage 写不进时的权威兜底）→
// 每个用例必须拿全新模块实例，否则上一个「写失败」用例残留的内存态会污染后面的用例。
async function loadHook() {
  vi.resetModules();
  return (await import("./label-toggle")).useLabelTogglePreference;
}

describe("useLabelTogglePreference (AI 标识开关记忆)", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("默认关：无存储 → false", async () => {
    const useHook = await loadHook();
    const { result } = renderHook(() => useHook());
    expect(result.current[0]).toBe(false);
  });

  it("set(true) → 内存态 true + 写回 localStorage", async () => {
    const useHook = await loadHook();
    const { result } = renderHook(() => useHook());
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
    expect(window.localStorage.getItem(KEY)).toBe("1");
  });

  it("记忆：已存 1 → 新实例 mount 后读回 true（下次进入沿用）", async () => {
    const useHook = await loadHook();
    window.localStorage.setItem(KEY, "1");
    const { result } = renderHook(() => useHook());
    expect(result.current[0]).toBe(true);
  });

  it("set(false) → 写回 0", async () => {
    const useHook = await loadHook();
    window.localStorage.setItem(KEY, "1");
    const { result } = renderHook(() => useHook());
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(window.localStorage.getItem(KEY)).toBe("0");
  });

  // ── FIX1 · 补「本次改造本身」的承重（此前零覆盖 = 假阴性：把 useSyncExternalStore 整个删回 useState 也全绿） ──

  // 🔴 跨面板同步：工作台六大面板改为常驻后，mount-only 的 useEffect 不再随切 tab 重跑 —— 这正是改用
  // useSyncExternalStore + 广播的**唯一动机**。变异：set 里去掉 listeners.forEach 广播 / subscribe 里去掉
  // listeners.add → 本条必红。
  it("两实例同步：A set(true) → B 的快照也变 true（跨面板一致，改造的目的）", async () => {
    const useHook = await loadHook();
    const a = renderHook(() => useHook());
    const b = renderHook(() => useHook());
    expect(b.result.current[0]).toBe(false);

    act(() => a.result.current[1](true));

    expect(a.result.current[0]).toBe(true);
    expect(b.result.current[0]).toBe(true);
  });

  // 🔴 P1-1：setItem 抛、getItem 正常 —— 隐私模式 / quota 满 / 只读模式的**典型形态**。
  // 权威已从 React state 迁到 readStored()，若内存态不是权威覆盖层（只在 getItem 的 catch 里读），
  // 这里会「写进内存、读却从 localStorage 拿回旧值」→ 开关点了不动，随后提交的 apply_visible_label 是错值。
  // 变异：readStored 改回「只在 getItem 抛时读 fallback」→ 本条必红。
  it("setItem 单独失败（getItem 正常）：set(true) → 快照仍变 true，且两实例都变；持久化失败但功能不失效", async () => {
    const useHook = await loadHook();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    const a = renderHook(() => useHook());
    const b = renderHook(() => useHook());

    act(() => a.result.current[1](true));

    expect(a.result.current[0]).toBe(true);
    expect(b.result.current[0]).toBe(true);
    // 持久化确实没成功（这是允许的）——但 UI 与提交值必须正确（这是不允许丢的）。
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("getItem 也抛（存储完全禁用）：首帧默认关 → set(true) 仍走内存态 true", async () => {
    const useHook = await loadHook();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("SecurityError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("SecurityError");
    });
    const { result } = renderHook(() => useHook());
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
  });

  // 内存兜底的清除策略（FIX1 的判断）：写成功即证明存储可用 → 内存态立即退位，否则一次偶发失败后的陈旧
  // 内存态会**永久压过** localStorage，连另一标签页经 storage 事件同步过来的新值也被吃掉。
  it("内存兜底在写成功后退位：先写失败(内存=true) → 存储恢复 → set(false) 落盘；此后外部改存储能被读到", async () => {
    const useHook = await loadHook();
    const setSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    const { result } = renderHook(() => useHook());
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true); // 内存态接管

    setSpy.mockRestore(); // 存储恢复可写
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(window.localStorage.getItem(KEY)).toBe("0"); // 真的落盘了

    // 关键：内存态已退位 —— 模拟另一标签页改了存储，新实例应读到新值而不是被陈旧内存态压过。
    window.localStorage.setItem(KEY, "1");
    const fresh = renderHook(() => useHook());
    expect(fresh.result.current[0]).toBe(true);
  });
});
