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

/**
 * presign URL 失效 → 重取一次的共享哨兵（HISTORY-VIDEO-DIALOG-UI-0001 · FIX1）。
 *
 * ## 为什么是共享 hook 而不是第 N 份拷贝
 *
 * 这条防线在本仓**已经被复制过，而且复制品之间已经分叉了**（实测，非转述）：
 *  - `video/video-player.tsx:20-27`：有 `useRef` 哨兵，**但没有 URL 变更重置** → 刷新后若新 URL 再失效，
 *    `expired.current` 恒 true → **永久哑掉**。注释还写着 "called at most once per render"，实际是 per **mount**。
 *  - `tasks/task-card.tsx:87-97`：注释自称 "mirrors VideoPlayer"，**却多了一个 URL 变更重置** → 行为其实
 *    与它「mirror」的对象**不一样**，且严格更好。
 *  - `video/video-detail.tsx:147` 的 `<img onError={handleUrlExpired}>`：**连哨兵都没有**。
 *
 * 也就是说：同一条防线的三处「拷贝」是三个行为。**拷贝不只是变多，它会悄悄漂移** ——
 * 而漂移没有任何测试拦得住，因为每处各测各的。这比「再多两份」严重得多，也正是必须收敛成一份的理由。
 *
 * ## 契约
 *
 * - **同一个 URL 只报一次**：浏览器对同一 src 可能连发多个 error，不能每个都触发重取。
 * - **URL 变了就重新给一次机会**：这是 `video-player.tsx` 缺的那半条（刷新后再过期要能再救）。
 * - **连续失败封顶**：见 `MAX_CONSECUTIVE_REFRESH` —— 这是 `task-card.tsx` 缺的那半条。
 * - **加载成功即清零**：长会话里播成功过之后再过期，仍能再救一次（否则封顶会误伤正常的二次过期）。
 *
 * ## 用法
 *
 * ```tsx
 * const media = useMediaUrlRefresh(task.playbackUrl, () => void query.refetch());
 * <video src={task.playbackUrl} onError={media.onError} onLoadedMetadata={media.onLoad} />
 * <img   src={url}              onError={media.onError} onLoad={media.onLoad} />
 * ```
 * `<video>` 用 `onLoadedMetadata` 而非 `onCanPlay`：本仓播放器都是 `preload="metadata"`，
 * 元数据拿到就证明这个 URL 签得开、取得到，不必等到能播。
 *
 * @param url      当前播放/显示地址（null/undefined 时 onError 直接哑火，没有 URL 就没有「过期」可言）
 * @param onExpired 重取动作（通常是 `query.refetch()` / `invalidateQueries`）
 */
export function useMediaUrlRefresh(url: string | null | undefined, onExpired: () => void) {
  /** 已经为哪个 URL 报过了（按值比对，不是布尔）→ 「同一 URL 只报一次」+「换了 URL 再给一次机会」一并成立。 */
  const firedFor = useRef<string | null>(null);
  /** 连续失败次数（成功加载即清零）。 */
  const consecutive = useRef(0);

  const onError = useCallback(() => {
    if (!url) return;
    if (firedFor.current === url) return; // 同一 URL 已报过 —— 含「重取后 BE 返回同一个 URL」的情形
    if (consecutive.current >= MAX_CONSECUTIVE_REFRESH) return; // 封顶 → 断死循环
    firedFor.current = url;
    consecutive.current += 1;
    onExpired();
  }, [url, onExpired]);

  const onLoad = useCallback(() => {
    consecutive.current = 0;
    firedFor.current = null;
  }, []);

  return { onError, onLoad };
}
