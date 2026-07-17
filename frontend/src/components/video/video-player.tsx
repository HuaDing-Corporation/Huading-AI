"use client";

import { useRef } from "react";
import { Download } from "lucide-react";

import { copy } from "@/lib/copy";

export interface VideoPlayerProps {
  playbackUrl?: string | null;
  downloadUrl?: string | null;
  poster?: string | null;
  onUrlExpired: () => void;
}

/**
 * Pure-props video player with download link. No hooks, no fetch.
 *
 * 🔴 **这是 presign 失效重取哨兵的最后一份手抄实现**（另见 `@/lib/media/use-media-url-refresh` —— 历史 tab 的
 * 卡片与两个弹窗已全部收敛到那个共享 hook）。本文件**没有迁过去**，理由见下；迁移已记 backlog。
 *
 * ⚠️ 已知缺陷（HISTORY-VIDEO-DIALOG-UI-0001 · FIX1 实测确认，**本包未修**）：
 *  1. 下面那句原注释写的是 "called at most once per **render**" —— **描述是错的**。`useRef` 跨 render 持久，
 *     真实语义是「每个组件实例**生命周期内**至多一次」。`task-card.tsx` 那句 "mirrors VideoPlayer" 很可能就是
 *     照这句错注释写的（它实际并没有 mirror：它多了一个 URL 变更重置，行为是本文件的**超集**）。
 *  2. **缺 URL 变更重置** → 唯一消费者 `video-detail.tsx:161` 把它接在会 `invalidateQueries` 的 query 上：
 *     URL 过期 → 重取 → 新 URL 若再过期，`expired.current` 恒 true → **永不再报，播放器哑死到组件卸载**。
 *
 * 为什么本包不修：① 它属 `/videos/{id}` 详情页域，本包范围是历史 tab；② **本文件零测试覆盖**，
 * 直接迁移属无网作业 —— 而它缺的那半条恰好在充当粗糙的死循环刹车，naive 补上重置反而会引入
 * 「BE 一直签出新的失效 URL → 无限重取」（共享 hook 用连续失败封顶解决了这个，见其注释）。
 * 迁移它 = 先补测试 + 修 bug + 改行为，是一片独立的活，不该塞进本 PR。
 */
export function VideoPlayer({ playbackUrl, downloadUrl, poster, onUrlExpired }: VideoPlayerProps) {
  const expired = useRef(false);

  function handleError() {
    if (!expired.current) {
      expired.current = true;
      onUrlExpired();
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <video
        controls
        preload="metadata"
        poster={poster ?? undefined}
        src={playbackUrl ?? undefined}
        onError={handleError}
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
