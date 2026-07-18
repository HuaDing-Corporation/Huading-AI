import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useMediaUrlRefreshScope } from "@/lib/media/use-media-url-refresh";

// MEDIA-URL-REFRESH-CONVERGE-0001 · FIX1 —— presign 失效重取哨兵的承重。
//
// 🔴 FIX1 的核心：**预算的作用域 = 被保护资源的作用域**，不是组件实例。
// 上一版每个媒体元素各持一份预算 → 列表上「本实例最多 2 次」变成 2×N 次真实请求（Codex B 的 P1-2）。
// 下面第 4/5 条钉的正是这个：**同一资源的并发失效只发一次重取**、**N 个元素共享一份封顶**。
//
// 期望值手写，不 import 被测代码的常量（MAX_CONSECUTIVE_REFRESH 故意不导出：
// 测试写死 2，改常量就必须回来改测试并解释为什么 —— 这是有意的摩擦）。

/** 一个可控完成时机的重取动作（在飞门控要靠 Promise 未决来生效）。 */
function deferredExpired() {
  const resolvers: Array<() => void> = [];
  const fn = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolvers.push(() => resolve());
      })
  );
  return { fn, settleAll: () => resolvers.splice(0).forEach((r) => r()) };
}

describe("useMediaUrlRefreshScope（presign 失效 → 重取的共享哨兵）", () => {
  it("同一个 URL 连报多次 error → 只重取一次（浏览器对同一 src 会连发 error）", () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));

    act(() => {
      result.current.onError("https://cdn/a.mp4?sig=1");
      result.current.onError("https://cdn/a.mp4?sig=1");
      result.current.onError("https://cdn/a.mp4?sig=1");
    });

    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("🔴 URL 换了 → 重新给一次机会（video-player 缺的正是这半条：它刷新后就永久哑了）", async () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));

    await act(async () => result.current.onError("https://cdn/a.mp4?sig=1"));
    expect(onExpired).toHaveBeenCalledTimes(1);

    // 重取回来一个新 URL，但它也失效了 → 必须还能再报（否则用户永远黑屏、且不知为何）
    await act(async () => result.current.onError("https://cdn/a.mp4?sig=2"));
    expect(onExpired).toHaveBeenCalledTimes(2);
  });

  it("🔴 无死循环：BE 每次都签出新 URL 但个个失效 → 连续失败封顶在 2 次，不再无限重取", async () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));

    // 模拟「对象已被删除」：每轮 error → 重取 → BE 签出**新** URL → 仍然 404 → error → ……
    for (let i = 1; i <= 10; i++) {
      await act(async () => result.current.onError(`https://cdn/gone.mp4?sig=${i}`));
    }

    // 救得回来的一次就够；救不回来的最多浪费 2 次 —— 而不是打后端 10 次、100 次。
    expect(onExpired).toHaveBeenCalledTimes(2);
  });

  // 🔴 P1-2 的正面承重之一：**并发合流**。
  // 列表里 N 张卡的 presign 同时过期 → N 个 error 在同一 tick 涌进来。一次 refetch 就能把
  // 整个列表的 URL 全换新 —— 其余 N-1 次是**纯粹的重复请求**。
  it("🔴 同一资源的并发失效（N 个不同 URL）→ 只发一次重取，不是 N 次", async () => {
    const { fn, settleAll } = deferredExpired();
    const { result } = renderHook(() => useMediaUrlRefreshScope(fn));

    act(() => {
      // 8 张卡 = 8 个独立媒体位置（forMedia），各自一个**各不相同**的 URL。
      // 挡住其余 7 次的是**在飞门控**（query 级），不是去重（那是各媒体各自的）。
      for (let i = 1; i <= 8; i++) result.current.forMedia(`card-${i}`).onError(`https://cdn/card-${i}.png?sig=1`);
    });

    expect(fn).toHaveBeenCalledTimes(1);

    // 这次重取回来了 —— 若新 URL 仍失效，才允许再发第二次（并被封顶挡在第三次）
    // ⚠️ 必须 await：门控在 Promise 的 .then 里解除（微任务），同步 act 不 flush 它。
    await act(async () => settleAll());
  });

  // 🔴 P1-2 的正面承重之二：**N 个媒体全坏 → 全列表合计 2 次**（合流 + 封顶的合成结果）。
  // 每媒体独立涨 consecutive（哪怕被在飞门控挡下也涨）→ 两轮后 8 个媒体全部到顶 → 第三轮整域封顶；
  // 而每轮真正的 refetch 由在飞门控合流成 1 次 → 合计 2 次。上一版每实例各持一份 → 8×2 = 16。
  it("🔴 N 个媒体全坏 → 全列表合计最多 2 次（不是 2×N）", async () => {
    const { fn, settleAll } = deferredExpired();
    const { result } = renderHook(() => useMediaUrlRefreshScope(fn));

    // 三轮「整列表全碎 → 重取 → 新 URL 仍全碎」。8 个独立媒体位置（forMedia）。
    for (let round = 1; round <= 3; round++) {
      act(() => {
        for (let i = 1; i <= 8; i++) result.current.forMedia(`card-${i}`).onError(`https://cdn/card-${i}.png?sig=${round}`);
      });
      await act(async () => settleAll()); // 上一次重取回来了，门控解除（微任务 → 必须 await）
    }

    expect(fn).toHaveBeenCalledTimes(2);
  });

  // 🔴 P1-2 的反面承重：**该隔离的地方要真的隔离**。
  // TaskList 里每张卡调 refreshTask(taskId) —— N 张卡 = N 个**不同资源**，N 次请求是必要的，
  // 不是浪费。若把它们也合流成 1 次，就只有一张卡被救回来，其余永远黑着。
  it("🔴 forKey 分区：每个元素各刷各的资源 → 各自一份预算，互不吃掉对方的机会", () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));

    act(() => {
      result.current.forKey("task-a").onError("https://cdn/a.mp4?sig=1");
      result.current.forKey("task-b").onError("https://cdn/b.mp4?sig=1");
    });

    // 两个不同资源 → 两次重取，且各自带着自己的 key（调用方靠它知道刷谁）
    expect(onExpired).toHaveBeenCalledTimes(2);
    expect(onExpired).toHaveBeenCalledWith("task-a");
    expect(onExpired).toHaveBeenCalledWith("task-b");
  });

  // 🔴 FIX2 的 P1-2：**健康兄弟不替坏图清账**。
  // FIX1 把封顶提到 query 级修好了 2N，但清零也跟着提上去了 → 列表里每张健康图片的 onLoad 清掉了
  // 坏图的失败计数 → 永久坏图无限重试（反向的洞）。拆分后：封顶/清零单媒体级，坏图独立爬到封顶就停。
  it("🔴 坏媒体 error + 健康兄弟 load × N 轮 → 坏媒体封顶仍 2（健康兄弟不替它清账）", async () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));
    const bad = result.current.forMedia("bad");
    const good = result.current.forMedia("good"); // 同一合流域内的健康兄弟

    for (let i = 1; i <= 5; i++) {
      await act(async () => bad.onError(`https://cdn/gone.png?sig=${i}`)); // 坏图每轮换新 URL、个个 404
      act(() => good.onLoad()); // 健康兄弟每轮都加载成功
    }

    // 坏图救不回来是它自己的事：爬到封顶 2 就停，不被健康兄弟的成功清账（清 query 级会变成无限）。
    expect(onExpired).toHaveBeenCalledTimes(2);
  });

  it("加载成功即清零 → 长会话里「播成功过、之后再过期」仍能再救（封顶不误伤正常的二次过期）", async () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));

    // 两次连续失败 → 已到封顶
    await act(async () => result.current.onError("https://cdn/a.mp4?sig=1"));
    await act(async () => result.current.onError("https://cdn/a.mp4?sig=2"));
    expect(onExpired).toHaveBeenCalledTimes(2);

    // 第 3 个 URL 真的播起来了 → 预算清零
    act(() => result.current.onLoad());

    // 很久以后它自己过期了 → 还能再救（若不清零，这里会哑 → 用户白等）
    await act(async () => result.current.onError("https://cdn/a.mp4?sig=3"));
    expect(onExpired).toHaveBeenCalledTimes(3);
  });

  it("URL 为 null（尚无播放地址）→ onError 哑火：没有 URL 就没有「过期」可言，别空打后端", () => {
    const onExpired = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));
    act(() => result.current.onError(null));
    expect(onExpired).not.toHaveBeenCalled();
  });

  // 重取失败（网络断了）也必须解除在飞门控 —— 否则这个资源永远卡在 inFlight，再也救不回来。
  it("重取本身失败 → 门控解除，后续新 URL 仍能再救（不把自己锁死）", async () => {
    const onExpired = vi.fn().mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useMediaUrlRefreshScope(onExpired));

    await act(async () => result.current.onError("https://cdn/a.mp4?sig=1"));
    expect(onExpired).toHaveBeenCalledTimes(1);

    await act(async () => result.current.onError("https://cdn/a.mp4?sig=2"));
    expect(onExpired).toHaveBeenCalledTimes(2);
  });
});
