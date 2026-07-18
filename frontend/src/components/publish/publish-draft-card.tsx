"use client";

import { useRef, useState } from "react";
import { Check, Copy, Download, ExternalLink } from "lucide-react";

import { copyToClipboard } from "@/lib/clipboard";
import { errorText } from "@/lib/api/error-text";
import { useMarkPublished } from "@/lib/api/hooks";
import type { PublishDraftItem } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 单平台发布草稿卡（PUBLISH-UI-0001）。消费后端 PublishDraftItem(platform_id/title/body/hashtags/
 * cover_url/media_url/publish_url)。标题/文案/话题本地可编辑(仅供复制/去发布，不回写后端)；一键复制；
 * 下载成片；「去XX发布」**仅 window.open 公开 publish_url**(不调发布 API、不碰社媒登录)；标记已发布
 * 经 PATCH(record_id + platform_id)。复制/标记防连点。
 */
export function PublishDraftCard({
  recordId,
  item,
  platformName
}: {
  recordId: string;
  item: PublishDraftItem;
  platformName: string;
}) {
  const mark = useMarkPublished();
  const [title, setTitle] = useState(item.title);
  const [body, setBody] = useState(item.body);
  const [hashtags, setHashtags] = useState(item.hashtags.join(" "));
  const [copied, setCopied] = useState(false);
  const [published, setPublished] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const copyingRef = useRef(false); // 复制防连点：写入期间同步锁

  const composed = [title.trim(), body.trim(), hashtags.trim()].filter(Boolean).join("\n");

  const onCopy = async () => {
    if (copyingRef.current) return;
    copyingRef.current = true;
    setError(null);
    // 🔴 CLIPBOARD-TRUTH-0001：成功态由 copyToClipboard 的返回布尔驱动。旧写法的 `?.` 在非安全上下文
    // 短路成 undefined、await 不抛 → 照样 setCopied(true) → 谎报。现在「缺 API」与「writeText 抛错」
    // 归一成同一条失败路径（本组件本就有 copyFailed 提示手动复制 —— 比静默降级更贴合发布场景）。
    if (await copyToClipboard(composed)) {
      setCopied(true);
      window.setTimeout(() => {
        copyingRef.current = false;
        setCopied(false);
      }, 1500);
    } else {
      copyingRef.current = false;
      setError(copy.publish.copyFailed);
    }
  };

  // 仅打开公开 publish_url，绝不调用任何发布 API / 社媒登录(合规)。
  const onGoPublish = () => {
    window.open(item.publish_url, "_blank", "noopener,noreferrer");
  };

  const onMark = async () => {
    if (mark.isPending || published) return;
    setError(null);
    try {
      await mark.mutateAsync({ recordId, platformId: item.platform_id });
      setPublished(true);
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-ink">{platformName}</span>
        <span
          className={`inline-flex items-center gap-1 rounded-pill border px-2.5 py-0.5 text-[11.5px] ${
            published ? "border-line-sel bg-chip-sel text-gold-deep" : "border-line-gold bg-glass-fill text-ink-soft"
          }`}
        >
          {published ? copy.publish.statusPublished : copy.publish.statusDraft}
        </span>
      </div>

      {/* 封面 / 成片预览 */}
      {item.cover_url && (
        <div className="overflow-hidden rounded-field border border-line-gold">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={item.cover_url} alt={copy.publish.coverAlt} className="aspect-video w-full object-cover" />
        </div>
      )}

      {/* 标题 */}
      <div>
        <label htmlFor={`pub-title-${item.platform_id}`} className={labelClass}>
          {copy.publish.cardTitleLabel}
        </label>
        <Input id={`pub-title-${item.platform_id}`} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>

      {/* 文案 */}
      <AiTextField id={`pub-body-${item.platform_id}`} label={copy.publish.cardTextLabel} value={body} onChange={setBody} rows={3} />

      {/* 话题 */}
      <div>
        <label htmlFor={`pub-tags-${item.platform_id}`} className={labelClass}>
          {copy.publish.cardTopicsLabel}
        </label>
        <Input id={`pub-tags-${item.platform_id}`} value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
      </div>

      {error && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {error}
        </p>
      )}

      {/* 动作行 */}
      <div className="flex flex-wrap gap-2">
        <Button variant="soft" size="sm" onClick={() => void onCopy()} disabled={copied}>
          {copied ? <Check size={14} strokeWidth={2} /> : <Copy size={14} strokeWidth={2} />}
          {copied ? copy.publish.copied : copy.publish.copyText}
        </Button>
        {item.media_url && (
          <a
            href={item.media_url}
            download
            className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[12.5px] text-gold-deep transition-colors hover:bg-glass-hover"
          >
            <Download size={14} strokeWidth={2} /> {copy.publish.download}
          </a>
        )}
        <Button variant="soft" size="sm" onClick={onGoPublish}>
          <ExternalLink size={14} strokeWidth={2} /> {copy.publish.goPublishAt(platformName)}
        </Button>
        <Button variant="primary" size="sm" onClick={() => void onMark()} disabled={mark.isPending || published}>
          {published ? copy.publish.marked : mark.isPending ? copy.publish.marking : copy.publish.markPublished}
        </Button>
      </div>
    </Card>
  );
}
