"use client";

import { Download } from "lucide-react";

import { copy } from "@/lib/copy";
import { useMediaUrlRefresh } from "@/lib/media/use-media-url-refresh";

export interface VideoPlayerProps {
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  poster?: string | null;
  onUrlExpired: () => void;
}

/**
 * 详情页视频播放器 + 下载链接。数据全从 props 来，本组件不 fetch。
 *
 * presign 失效重取走**共享哨兵** `useMediaUrlRefresh`（MEDIA-URL-REFRESH-CONVERGE-0001）。
 * 本文件曾是全仓**最后一份手抄实现**，迁移把它身上的三个 bug 一次带走：
 *  1. **缺 URL 变更重置** → 唯一消费者 `video-detail.tsx` 把 onUrlExpired 接在会 `invalidateQueries`
 *     的 query 上：URL 过期 → 重取 → 新 URL 若再过期，旧的 `expired.current` 恒 true →
 *     **永不再报，播放器哑死到组件卸载**。hook 按 URL **值**比对（不是布尔），换了 URL 自动再给一次机会。
 *  2. **缺 URL 存在性守卫** → `playbackUrl=null` 时报 error 照样调 `onUrlExpired()` → 父组件
 *     invalidateQueries → **白打一次后端**（没有 URL 就没有「过期」可言）。hook 有 `if (!url) return`。
 *  3. 原注释写 "called at most once per **render**" —— 实际是 per **mount**（`useRef` 跨 render 持久）。
 *     `task-card.tsx` 那句 "mirrors VideoPlayer" 很可能就是照这句错注释写的（它并没有 mirror：
 *     它多了一个 URL 变更重置，行为是本文件的超集）。**复制传播的不只是代码，是误解** ——
 *     这个 bug 的修法就是删掉那句话：注释不是防线，改对了也还是注释。
 *
 * 🔴 **修 1 的前提是「封顶」已经在**：单独补重置 → 对象已删时（BE 每次都签得出新 URL、个个 404）
 * error → 重取 → 新 URL → error → …… **无限重取**，每轮真打一次后端；原来缺的那半条恰好在充当
 * 粗糙的死循环刹车。这个顺序不靠「记得先做封顶」来保证 —— hook 把「URL 变更重置」与「连续失败封顶
 * + 加载成功即清零」**打包在同一次迁移里**，结构上不存在「重置已开、封顶未到」的中间态。
 * 承重见 `video-player.test.tsx`（封顶那条拆掉 hook 的 MAX_CONSECUTIVE_REFRESH 即红）。
 */
export function VideoPlayer({ playbackUrl, downloadUrl, poster, onUrlExpired }: VideoPlayerProps) {
  const media = useMediaUrlRefresh(playbackUrl, onUrlExpired);

  return (
    <div className="flex flex-col gap-3">
      <video
        controls
        preload="metadata"
        poster={poster ?? undefined}
        src={playbackUrl ?? undefined}
        onError={media.onError}
        onLoadedMetadata={media.onLoad}
        className="max-h-[480px] w-full rounded-field border border-line-gold bg-black/5"
      />
      {downloadUrl && (
        <a
          href={downloadUrl}
          download
          className="inline-flex w-fit items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <Download size={15} strokeWidth={2} />
          {copy.detail.download}
        </a>
      )}
    </div>
  );
}
