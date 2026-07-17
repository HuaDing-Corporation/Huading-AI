"use client";

import { Download } from "lucide-react";

import { historyImageDimensions, type HistoryImageSetItem } from "@/lib/api/history-images";
import { copy } from "@/lib/copy";
import { useMediaUrlRefresh } from "@/lib/media/use-media-url-refresh";

/**
 * 图片历史·整套单张（HISTORY-UI-0001；FIX2 对齐真实 BE）——复用详情图红线：预览仅 CSS 等比缩放同一原图 URL
 * （object-contain，零 canvas/crop/resize）；下载 `<a href={download_url} download>` 给原始 bytes；显示原始尺寸。
 * FIX2：真实 BE 详情**只返成功张、失败张已 omit**（service:106,252 `status=="succeeded"`；schema download_url:str 非空），
 * 故不再有「缺图禁用」分支（那是照冻结文档臆造的、BE 现状不存在的形态）。theme（详情图机器键）本地化展示。
 *
 * MEDIA-URL-REFRESH-CONVERGE-0001（第 5 片）：`download_url` 是 presign（history-images.ts:41），此前**零防护**。
 * ⚠️ 任务包 §二.3 点的是「history-detail-dialog 零 onError」，但那个文件是**纯外壳、没有 img** ——
 * 详情弹窗里真正裸着的 `<img>` 是本文件。以源码为准。
 */
export function HistoryImageTile({
  item,
  onUrlError
}: {
  item: HistoryImageSetItem;
  /** presign 失效 → 请调用方重取（HistorySetDialog 的 `query.refetch()`）。整套 N 张各是一个实例 → 各自一个哨兵。 */
  onUrlError: () => void;
}) {
  const media = useMediaUrlRefresh(item.download_url, onUrlError);
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
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={item.download_url}
        alt={copy.historyImages.previewAlt(pageNo)}
        onError={media.onError}
        onLoad={media.onLoad}
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
    </div>
  );
}
