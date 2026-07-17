import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useMediaUrlRefresh } from "@/lib/media/use-media-url-refresh";

// HISTORY-VIDEO-DIALOG-UI-0001 · FIX1 —— presign 失效重取哨兵的承重。
// 这四条正是本仓三处「拷贝」各自缺的那半条：
//   · video-player.tsx 缺「URL 变了再给一次机会」→ 第 2 条钉它
//   · task-card.tsx    缺「连续失败封顶」        → 第 3 条钉它
//   · video-detail.tsx 的 <img> 连哨兵都没有     → 第 1 条钉它
// 期望值手写，不 import 被测代码的常量（MAX_CONSECUTIVE_REFRESH 故意不导出：
// 测试写死 2，改常量就必须回来改测试并解释为什么 —— 这是有意的摩擦）。

describe("useMediaUrlRefresh（presign 失效 → 重取一次的共享哨兵）", () => {
  it("同一个 URL 连报多次 error → 只重取一次（浏览器对同一 src 会连发 error）", () => {
    const onExpired = vi.fn();
    const { result } = renderHook(() => useMediaUrlRefresh("https://cdn/a.mp4?sig=1", onExpired));

    act(() => {
      result.current.onError();
      result.current.onError();
      result.current.onError();
    });

    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("🔴 URL 换了 → 重新给一次机会（video-player.tsx 缺的正是这半条：它刷新后就永久哑了）", () => {
    const onExpired = vi.fn();
    const { result, rerender } = renderHook(({ url }) => useMediaUrlRefresh(url, onExpired), {
      initialProps: { url: "https://cdn/a.mp4?sig=1" }
    });

    act(() => result.current.onError());
    expect(onExpired).toHaveBeenCalledTimes(1);

    // 重取回来一个新 URL，但它也失效了 → 必须还能再报（否则用户永远黑屏、且不知为何）
    rerender({ url: "https://cdn/a.mp4?sig=2" });
    act(() => result.current.onError());
    expect(onExpired).toHaveBeenCalledTimes(2);
  });

  it("🔴 无死循环：BE 每次都签出新 URL 但个个失效 → 连续失败封顶在 2 次，不再无限重取", () => {
    const onExpired = vi.fn();
    const { result, rerender } = renderHook(({ url }) => useMediaUrlRefresh(url, onExpired), {
      initialProps: { url: "https://cdn/gone.mp4?sig=1" }
    });

    // 模拟「对象已被删除」：每轮 error → 重取 → BE 签出**新** URL → 仍然 404 → error → ……
    for (let i = 1; i <= 10; i++) {
      rerender({ url: `https://cdn/gone.mp4?sig=${i}` });
      act(() => result.current.onError());
    }

    // 救得回来的一次就够；救不回来的最多浪费 2 次 —— 而不是打后端 10 次、100 次。
    expect(onExpired).toHaveBeenCalledTimes(2);
  });

  it("加载成功即清零 → 长会话里「播成功过、之后再过期」仍能再救（封顶不误伤正常的二次过期）", () => {
    const onExpired = vi.fn();
    const { result, rerender } = renderHook(({ url }) => useMediaUrlRefresh(url, onExpired), {
      initialProps: { url: "https://cdn/a.mp4?sig=1" }
    });

    // 两次连续失败 → 已到封顶
    act(() => result.current.onError());
    rerender({ url: "https://cdn/a.mp4?sig=2" });
    act(() => result.current.onError());
    expect(onExpired).toHaveBeenCalledTimes(2);

    // 第 3 个 URL 真的播起来了 → 预算清零
    rerender({ url: "https://cdn/a.mp4?sig=3" });
    act(() => result.current.onLoad());

    // 很久以后它自己过期了 → 还能再救（若不清零，这里会哑 → 用户白等）
    act(() => result.current.onError());
    expect(onExpired).toHaveBeenCalledTimes(3);
  });

  it("URL 为 null（尚无播放地址）→ onError 哑火：没有 URL 就没有「过期」可言，别空打后端", () => {
    const onExpired = vi.fn();
    const { result } = renderHook(() => useMediaUrlRefresh(null, onExpired));
    act(() => result.current.onError());
    expect(onExpired).not.toHaveBeenCalled();
  });
});
