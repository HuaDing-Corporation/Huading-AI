"use client";

import { Download } from "lucide-react";

import { historyImageDimensions, type HistoryImageSetItem } from "@/lib/api/history-images";
import { copy } from "@/lib/copy";

/**
 * 图片历史·整套单张（HISTORY-UI-0001）——复用详情图红线：预览仅 CSS 等比缩放同一原图 URL（object-contain，零
 * canvas/crop/resize）；下载 `<a href={download_url} download>` 给原始 bytes；显示原始尺寸（width/height 缺失即
 * 「以下载文件为准」，不冒充）；download_url 缺失 → 禁用态而非死链。theme（详情图机器键）本地化展示。
 */
export function HistoryImageTile({ item }: { item: HistoryImageSetItem }) {
  const pageNo = item.index + 1;
  const dims = historyImageDimensions(item);
  const sizeHint = dims ? copy.historyImages.sizeLabel(dims) : copy.historyImages.sizeUnknown;
  // 小标注：优先友好 label；否则若是详情图 theme 机器键 → 复用既有本地化（未知键原样透出）。
  const caption = item.label ?? (item.theme ? copy.workbench.ecomReplicateTheme(item.theme) : null);
  return (
    <div className="flex flex-col gap-2 rounded-field border border-line-gold bg-glass-fill p-2.5">
      <div className="flex items-center justify-between text-[12px] text-ink-soft">
        <span>{copy.historyImages.setPageNo(pageNo)}</span>
        {caption ? <span className="text-ink-faint">{caption}</span> : null}
      </div>
      {item.download_url ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={item.download_url}
            alt={copy.historyImages.previewAlt(pageNo)}
            className="w-full rounded-mark border border-line-gold object-contain"
          />
          <p className="text-[11.5px] leading-relaxed text-ink-faint">{sizeHint}</p>
          <a
            href={item.download_url}
            download={`history-${pageNo}.png`}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 text-[13px] text-ink-soft outline-none transition-colors hover:bg-white/60 focus-visible:shadow-focus-gold"
          >
            <Download size={14} strokeWidth={2} /> {copy.historyImages.download}
          </a>
        </>
      ) : (
        <>
          <p className="text-[11.5px] leading-relaxed text-ink-faint">{sizeHint}</p>
          <span
            aria-disabled="true"
            className="inline-flex h-9 cursor-not-allowed items-center justify-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 text-[13px] text-ink-faint opacity-60"
          >
            <Download size={14} strokeWidth={2} /> {copy.historyImages.downloadUnavailable}
          </span>
        </>
      )}
    </div>
  );
}
