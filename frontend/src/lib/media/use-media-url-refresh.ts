"use client";

import { useCallback, useRef } from "react";

/**
 * 连续失败上限 —— **断死循环用**，不是性能优化。
 *
 * 场景：presign URL 失效有两种，肉眼分不出，`onError` 收到的东西一模一样：
 *  ① **过期**（TTL 到了）→ 重取一个新 URL 就好了 —— 这正是本 hook 存在的理由；
 *  ② **对象没了**（媒体被删/迁移）→ BE 每次都能**签出一个新 URL**，但每个都 404。
 * 若只按「URL 变了就再报一次」放行，②会变成 error → 重取 → 新 URL → error → …… **无限循环**。
 * 故连续失败必须封顶：救得回来的（①）一次就够，救不回来的（②）最多浪费 2 次。
 */
const MAX_CONSECUTIVE_REFRESH = 2;

/**
 * 单个**媒体位置**的失败预算（封顶 + 去重 + 清零都在这一级）。
 * 「媒体位置」的身份由调用方给的 mediaKey 决定 —— 坏图的 URL 每次重取都换新，但它一直是同一个媒体位置，
 * 封顶要在这个位置上累计。**mediaKey 合不合格有硬判据，见 `forMedia` 的注释。**
 */
interface MediaBudget {
  /** 已为哪些 URL 报过（按值比对）→「同一 URL 只报一次」+「换了 URL 再给一次机会」一并成立。 */
  fired: Set<string>;
  /** 连续失败次数（本媒体加载成功即清零）。 */
  consecutive: number;
}

/** 一个**合流域**（= 一次重取能一起刷新的那批媒体，通常是一个 query）。 */
interface RefreshGroup {
  /** 已有一次重取在路上 —— 它回来会把该域下**所有** URL 一起换新，故此刻其它媒体的失效不必再各发一次。 */
  inFlight: boolean;
  /** 域内每个媒体位置各自的封顶预算。 */
  media: Map<string, MediaBudget>;
}

/** 媒体元素侧的接口：URL 是元素自己的，预算的作用域由上游决定。 */
export interface MediaUrlRefreshScope {
  /** 本元素的 URL 加载失败了。 */
  onError: (url: string | null | undefined) => void;
  /** 本元素的 URL 加载成功了 → **只清本媒体**的失败预算（不碰同域内其它媒体）。 */
  onLoad: () => void;
}

/** 持有 query 的组件拿到的东西：可整份下发（单媒体），或按媒体位置 / 按独立资源切分。 */
export interface MediaUrlRefreshGroup extends MediaUrlRefreshScope {
  /**
   * 同一合流域内的一个**独立媒体位置**：合流（在飞）与它同域的兄弟**共享**，但封顶/去重/清零**独立**。
   * 用于「一次 refetch 刷回整批 URL、但每个媒体各自可能好可能坏」的列表（history-grid / 整套图 / 视频历史）。
   *
   * ## 🔴 mediaKey 的判据：**跨重取的双射**（FIX3 —— 这条判据本身错过一次，代价是一个 P1）
   *
   * 上一版这里写的是「mediaKey 用跨刷新稳定的身份（**item.id / index**）」。**那个括号是错的**，而且
   * `history-set-dialog` 就是照着它把 `it.index` 传了进来（FIX3 的 P1）。判据不能是**举例**，得是**性质**：
   *
   *  | 语义 | 要求 | 违反后果 |
   *  |---|---|---|
   *  | **封顶** | **稳定**：跨重取，同一个物理媒体 → **同一个** key | 预算换到新 key → **绕过封顶**（FIX3 的 P1）|
   *  | **清零** | **单射**：同域内，不同媒体 → **不同** key | 健康兄弟的 onLoad 替坏图清账 → **永不封顶**（FIX2 的 P1-2）|
   *
   * 两条缺一不可，且**方向相反** —— 这正是 `index` 骗过复查的原因：它**完美满足单射**（enumerate 出来的
   * 位置在一次响应内必然两两不同），只违反稳定。而**单射性看一份响应就能看出来，稳定性看一份响应根本
   * 看不出来** —— 它是两次响应之间的性质。任何「盯着一个响应/一段代码看它像不像 id」的复查都会放行它。
   *
   * ## 所以判据是**可执行的**，不是 review 问题
   *
   * 「复查了但判据错」没有任何流程拦得住（复查已经做了、看起来有据可查）。唯一拦得住的是让它**自己变红**：
   *
   * > 给持有 query 的组件**两次连续响应**，其中**同一个物理媒体处在不同位置**、URL 已换新；
   * > 断言**真实的 adapter 调用次数**（不是 refetch 的 spy 次数）仍等于封顶值。
   *
   * key 稳不稳定是**产出它的那个端点**的性质，不是本 hook 的性质 → 判据只能**逐调用点**用场景测试兑现。
   * 现成的两条：`history-set-dialog.network.test.tsx`（index 漂移）/ `history-grid.network.test.tsx`（部分成功）。
   */
  forMedia: (mediaKey: string) => MediaUrlRefreshScope;
}

/** 根：额外能按**独立资源**切一个新合流域。 */
export interface MediaUrlRefreshRoot extends MediaUrlRefreshGroup {
  /**
   * 切一个**独立的合流域**（连「在飞」都不共享）。用于「每个元素刷的是不同 query/资源」——
   * 如 TaskList 的 `refreshTask(taskId)`：刷 task A 和刷 task B 是两个不同的后端请求，**都必要、不该合流**。
   * 反之「一次 refetch 刷整个列表」的场景用 `forMedia`（共享在飞），别用 `forKey`。
   */
  forKey: (budgetKey: string) => MediaUrlRefreshGroup;
}

/**
 * presign URL 失效 → 重取的共享哨兵（MEDIA-URL-REFRESH-CONVERGE-0001 · FIX1/FIX2）。
 *
 * ## 🔴 三个语义，**三个不同的作用域**（FIX2 把它们彻底拆开）
 *
 * 「作用域」这件事在本 hook 上错过两次、两个方向：
 *  - FIX1 前：封顶**太窄**（每个组件实例一份）→ 同一 query 的 N 个媒体 = **2×N 次请求**。
 *  - FIX1 后 / FIX2 前：把封顶提到 query 级修好了 2N，但**清零也跟着提上去了** → 列表里每张**健康**图片
 *    的 `onLoad` 清掉了**坏图**的失败计数 → 永久坏图**无限重试**（反向的洞）。
 *
 * 所以三个语义各归各的作用域：
 *  | 语义 | 作用域 | 理由 |
 *  |---|---|---|
 *  | **合流（在飞 / 去重）** | **合流域（query 级）** | 一次 refetch 刷回整批 URL → 同批失效只该发一次 |
 *  | **失败封顶** | **单媒体级** | 坏图救不回来是**它自己**的事，不该拖累/被拖累兄弟 |
 *  | **成功清零** | **单媒体级** | 健康图片成功只证明**它自己**好，不能替坏图清账 |
 *
 * ## 为什么「全坏」仍是 2 次，而不是 2×N —— 封顶单媒体、refetch 却合流
 *
 * `consecutive` 每个媒体独立累计，**即使这次失效被在飞门控挡下、没真发 refetch，它也照涨**。于是
 * 「8 张全坏」两轮后 8 个媒体的 consecutive 全部到顶 → 第三轮整域封顶；而每一轮真正的 refetch 由
 * `inFlight` 合流成 1 次 → 合计 **2** 次。「1 坏 7 好」时坏图独立爬到 2 就停，健康兄弟的 load 只清自己。
 * 两个场景用同一套规则同时成立 —— 这正是上两版顾此失彼的那个平衡点。
 *
 * ## 作用域只能由调用方决定
 *
 * 同一个 `TaskCard`：在 `generation-history` 里 N 张卡共享一个 `useVideoHistory` query（一次 refetch 刷
 * 全部）→ 调用方传 `refresh.forMedia(item.id)`（共享在飞、独立封顶）；在 `task-list` 里每张卡各刷各的
 * `refreshTask(taskId)`（不同请求）→ 传 `refresh.forKey(taskId)`（连在飞都独立）。组件自己无从知道。
 *
 * ## 用法
 *
 * ```tsx
 * // 同一 query、多媒体（一次 refetch 刷全部）：共享在飞、各自封顶
 * const refresh = useMediaUrlRefreshScope(() => query.refetch());
 * {items.map((it) => <Card key={it.id} item={it} refresh={refresh.forMedia(it.id)} />)}
 *
 * // 每个元素刷不同资源：连在飞都独立
 * const refresh = useMediaUrlRefreshScope((taskId) => refreshTask(taskId));
 * {tasks.map((t) => <TaskCard key={t.id} task={t} refresh={refresh.forKey(t.taskId)} />)}
 *
 * // 元素侧（叶子只报告自己的 URL，拿不到也建不了预算）
 * <video src={url} onError={() => refresh.onError(url)} onLoadedMetadata={refresh.onLoad} />
 * <img   src={url} onError={() => refresh.onError(url)} onLoad={refresh.onLoad} />
 * ```
 * `<video>` 用 `onLoadedMetadata` 而非 `onLoad`：**React 的 media 事件表里没有 load**，`<video onLoad>`
 * 根本接不上（实测 `fireEvent.load(video)` 静默不触发）；而元数据到手就证明这个 URL 签得开、取得到。
 *
 * @param onExpired 重取动作，**必须返回 Promise**（`query.refetch()` / `invalidateQueries()` 都返回）——
 *                  在飞门控靠它落定判断「这次重取回来了没有」。返回 `void` 会让门控立刻解除、退化成并发
 *                  重取，故类型上强制 Promise：**编译器替人记，不靠纪律**。收到该次失效所属的 `budgetKey`。
 */
export function useMediaUrlRefreshScope(
  onExpired: (budgetKey: string) => Promise<unknown>
): MediaUrlRefreshRoot {
  const groups = useRef<Map<string, RefreshGroup>>(new Map());

  const groupOf = (budgetKey: string): RefreshGroup => {
    let group = groups.current.get(budgetKey);
    if (!group) {
      group = { inFlight: false, media: new Map() };
      groups.current.set(budgetKey, group);
    }
    return group;
  };

  const report = useCallback(
    (budgetKey: string, mediaKey: string, url: string | null | undefined) => {
      if (!url) return; // 没有 URL 就没有「过期」可言，别空打后端
      const group = groupOf(budgetKey);
      let media = group.media.get(mediaKey);
      if (!media) {
        media = { fired: new Set(), consecutive: 0 };
        group.media.set(mediaKey, media);
      }
      if (media.fired.has(url)) return; // 这个 URL 已报过 —— 含「重取后 BE 返回同一个 URL」的情形
      if (media.consecutive >= MAX_CONSECUTIVE_REFRESH) return; // 本媒体封顶 → 断死循环
      media.fired.add(url);
      media.consecutive += 1; // 🔴 每媒体独立涨 —— **哪怕接下来被在飞门控挡下**，也算它自己失败了一次
      if (group.inFlight) return; // 🔴 合流：同域已有一次重取在路上，它会把这个 URL 也换掉
      group.inFlight = true;
      const settle = () => {
        group.inFlight = false;
      };
      onExpired(budgetKey).then(settle, settle);
    },
    [onExpired]
  );

  const succeed = useCallback((budgetKey: string, mediaKey: string) => {
    // 🔴 只清**本媒体**：健康图片的成功只证明它自己好，不替同域的坏图清账（FIX2 的 P1-2）。
    const media = groups.current.get(budgetKey)?.media.get(mediaKey);
    if (!media) return;
    media.consecutive = 0;
    media.fired.clear();
  }, []);

  const scopeOf = useCallback(
    (budgetKey: string, mediaKey: string): MediaUrlRefreshScope => ({
      onError: (url) => report(budgetKey, mediaKey, url),
      onLoad: () => succeed(budgetKey, mediaKey)
    }),
    [report, succeed]
  );

  const groupApi = useCallback(
    (budgetKey: string): MediaUrlRefreshGroup => ({
      ...scopeOf(budgetKey, ""),
      forMedia: (mediaKey) => scopeOf(budgetKey, mediaKey)
    }),
    [scopeOf]
  );

  return {
    ...groupApi(""),
    forKey: (budgetKey) => groupApi(budgetKey)
  };
}
