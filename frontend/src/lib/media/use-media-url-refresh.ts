"use client";

import { useCallback, useRef } from "react";

/**
 * 连续失败上限 —— **断死循环用**，不是性能优化。
 *
 * 场景：presign URL 失效有两种，肉眼分不出，`onError` 收到的东西一模一样：
 *  ① **过期**（TTL 到了）→ 重取一个新 URL 就好了 —— 这正是本 hook 存在的理由；
 *  ② **对象没了**（媒体被删/迁移）→ BE 每次都能**签出一个新 URL**，但每个都 404。
 * 若只按「URL 变了就再报一次」放行，②会变成 error → 重取 → 新 URL → error → …… **无限循环**，
 * 每轮都真打一次后端。故连续失败必须封顶：救得回来的（①）一次就够，救不回来的（②）最多浪费 2 次。
 */
const MAX_CONSECUTIVE_REFRESH = 2;

/** 一份重取预算 —— 归属于**一个被保护的资源**（见 `useMediaUrlRefreshScope` 的 budgetKey）。 */
interface Budget {
  /** 已经为哪些 URL 报过（按值比对）→「同一 URL 只报一次」+「换了 URL 再给一次机会」一并成立。 */
  fired: Set<string>;
  /** 连续失败次数（加载成功即清零）。 */
  consecutive: number;
  /** 已有一次重取在路上 —— 它回来会把该资源下**所有** URL 一起换新，故此刻的其它失效不必再发。 */
  inFlight: boolean;
}

/** 媒体元素侧的接口：URL 是元素自己的，**预算是资源的**。 */
export interface MediaUrlRefreshScope {
  /** 本元素的 URL 加载失败了。 */
  onError: (url: string | null | undefined) => void;
  /** 本元素的 URL 加载成功了 → 该资源的预算清零。 */
  onLoad: () => void;
}

/** 持有 query 的组件拿到的东西：它可以整份下发（共享预算），也可以按资源身份切分（`forKey`）。 */
export interface MediaUrlRefreshRoot extends MediaUrlRefreshScope {
  /**
   * 按**被保护资源的身份**切一份独立预算。
   * 用于「每个元素各刷各的资源」（如 TaskList 的 `refreshTask(taskId)` —— N 张卡 = N 个资源，
   * N 次请求是**必要的**，不是浪费）。反之「一次重取刷整个列表」的场景**不要**切 —— 那正是要合流的。
   */
  forKey: (budgetKey: string) => MediaUrlRefreshScope;
}

/**
 * presign URL 失效 → 重取的共享哨兵（MEDIA-URL-REFRESH-CONVERGE-0001 · FIX1）。
 *
 * ## 🔴 FIX1 修的是「作用域」，不是「数字」
 *
 * 上一版 `useMediaUrlRefresh(url, onExpired)` 让**每个媒体元素各持一份预算**，而
 * 它们调用的 `onExpired` 常常指向**同一个 query**（一次 refetch 刷回整个列表的 URL）。
 * 于是「本实例最多 2 次」在列表上变成 **2×N 次真实请求** —— **局部正确、全局错**。
 * （更糟：TanStack 默认 `cancelRefetch:true`，两次并发 refetch 实测 queryFn 被调 **3** 次
 *  —— 第二次**中止并重启**第一次。所以「2 次并发」比「2 次」还贵。）
 *
 * 修法不是把数字改小，是**让预算的作用域 = 被保护资源的作用域**：
 *  - 预算（去重 / 封顶 / 在飞门控）挂在 **`budgetKey`** 上，不挂在组件实例上；
 *  - 谁持有 query，谁 `useMediaUrlRefreshScope` 并把 scope **下发**给所有消费该 query 的元素；
 *  - 元素侧只有 `onError(url)` / `onLoad()`，**拿不到也建不了自己的预算** → 结构上无法退回 2×N。
 *
 * 为什么作用域只能由调用方决定：同一个 `TaskCard`，
 *  - 在 `task-list.tsx` 里接 `refreshTask(taskId)` → 资源是**单个 task** → 每张卡一份预算（`forKey`）；
 *  - 在 `generation-history.tsx` 里接 `query.refetch()` → 资源是**整个列表 query** → 全部共享一份。
 * 组件自己无从知道这件事 —— 这正是上一版把预算放在组件里就必然错的原因。
 *
 * ## 契约（逐条都有承重，见 use-media-url-refresh.test.ts）
 *
 * - **同一个 URL 只报一次**：浏览器对同一 src 可能连发多个 error。
 * - **URL 变了就重新给一次机会**：刷新后新 URL 再过期要能再救。
 * - **同一资源的并发失效只发一次重取**（`inFlight`）：N 张卡同时碎 → **1 次**请求，不是 N 次。
 *   这也让同一 scope 内**永不出现并发 refetch** → `cancelRefetch` 在本链路上无从生效，故不显式设置。
 * - **连续失败封顶**：见 `MAX_CONSECUTIVE_REFRESH`。
 * - **加载成功即清零**：长会话里播成功过之后再过期，仍能再救一次。
 *
 * ## 用法
 *
 * ```tsx
 * // 持有 query 的组件（一次 refetch 刷全列表 → 全部共享一份预算）
 * const refresh = useMediaUrlRefreshScope(() => query.refetch());
 * {items.map((it) => <Card key={it.id} item={it} refresh={refresh} />)}
 *
 * // 每个元素各刷各的资源 → 按资源身份切分
 * const refresh = useMediaUrlRefreshScope((taskId) => refreshTask(taskId));
 * {tasks.map((t) => <TaskCard key={t.id} task={t} refresh={refresh.forKey(t.taskId)} />)}
 *
 * // 元素侧
 * <video src={url} onError={() => refresh.onError(url)} onLoadedMetadata={refresh.onLoad} />
 * <img   src={url} onError={() => refresh.onError(url)} onLoad={refresh.onLoad} />
 * ```
 * `<video>` 用 `onLoadedMetadata` 而非 `onLoad`：**React 的 media 事件表里没有 load**，`<video onLoad>`
 * 根本接不上（实测 `fireEvent.load(video)` 静默不触发）；而元数据到手就证明这个 URL 签得开、取得到。
 *
 * @param onExpired 重取动作，**必须返回 Promise**（`query.refetch()` / `invalidateQueries()` 都返回）——
 *                  在飞门控靠它判断「这次重取回来了没有」。返回 `void` 会让门控立刻解除、退化成并发重取，
 *                  故类型上强制 Promise：**编译器替人记，不靠纪律**。收到的是该次失效所属的 `budgetKey`。
 */
export function useMediaUrlRefreshScope(
  onExpired: (budgetKey: string) => Promise<unknown>
): MediaUrlRefreshRoot {
  const budgets = useRef<Map<string, Budget>>(new Map());

  const report = useCallback(
    (budgetKey: string, url: string | null | undefined) => {
      if (!url) return; // 没有 URL 就没有「过期」可言，别空打后端
      let budget = budgets.current.get(budgetKey);
      if (!budget) {
        budget = { fired: new Set(), consecutive: 0, inFlight: false };
        budgets.current.set(budgetKey, budget);
      }
      if (budget.fired.has(url)) return; // 这个 URL 已报过 —— 含「重取后 BE 返回同一个 URL」的情形
      budget.fired.add(url);
      if (budget.inFlight) return; // 🔴 合流：已有一次重取在路上，它会把这个 URL 也换掉
      if (budget.consecutive >= MAX_CONSECUTIVE_REFRESH) return; // 封顶 → 断死循环
      budget.consecutive += 1;
      budget.inFlight = true;
      const settled = () => {
        budget.inFlight = false;
      };
      onExpired(budgetKey).then(settled, settled);
    },
    [onExpired]
  );

  const succeed = useCallback((budgetKey: string) => {
    const budget = budgets.current.get(budgetKey);
    if (!budget) return;
    budget.consecutive = 0;
    budget.fired.clear();
  }, []);

  const forKey = useCallback(
    (budgetKey: string): MediaUrlRefreshScope => ({
      onError: (url) => report(budgetKey, url),
      onLoad: () => succeed(budgetKey)
    }),
    [report, succeed]
  );

  return {
    onError: (url) => report("", url),
    onLoad: () => succeed(""),
    forKey
  };
}
