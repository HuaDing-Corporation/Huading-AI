"use client";

import { useRef, useState } from "react";
import { Check, Copy, Download, ExternalLink } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useMarkPublished } from "@/lib/api/hooks";
import type { PublishRecord } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 单平台发布草稿卡（PUBLISH-UI-0001）。标题/文案/话题本地可编辑(仅供复制/去发布，不回写后端)；
 * 一键复制文案到剪贴板；下载成片；「去XX发布」**仅 window.open 公开 publish_url**(不调发布 API、
 * 不碰社媒登录)；标记已发布发 PATCH。复制/标记防连点。
 */
export function PublishDraftCard({ record, platformName }: { record: PublishRecord; platformName: string }) {
  const mark = useMarkPublished();
  const [title, setTitle] = useState(record.title);
  const [text, setText] = useState(record.text);
  const [topics, setTopics] = useState(record.topics.join(" "));
  const [copied, setCopied] = useState(false);
  const [published, setPublished] = useState(record.status === "published");
  const [error, setError] = useState<string | null>(null);
  const copyingRef = useRef(false); // 复制防连点：写入期间同步锁，避免连点重复 writeText

  const composed = [title.trim(), text.trim(), topics.trim()].filter(Boolean).join("\n");

  const onCopy = async () => {
    if (copyingRef.current) return;
    copyingRef.current = true;
    setError(null);
    try {
      await navigator.clipboard?.writeText(composed);
      setCopied(true);
      window.setTimeout(() => {
        copyingRef.current = false;
        setCopied(false);
      }, 1500);
    } catch {
      copyingRef.current = false;
      setError(copy.publish.copyFailed);
    }
  };

  // 仅打开公开 publish_url，绝不调用任何发布 API / 社媒登录(合规)。
  const onGoPublish = () => {
    window.open(record.publish_url, "_blank", "noopener,noreferrer");
  };

  const onMark = async () => {
    if (mark.isPending || published) return;
    setError(null);
    try {
      await mark.mutateAsync(record.id);
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
      {record.cover_url && (
        <div className="overflow-hidden rounded-field border border-line-gold">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={record.cover_url} alt={copy.publish.coverAlt} className="aspect-video w-full object-cover" />
        </div>
      )}

      {/* 标题 */}
      <div>
        <label htmlFor={`pub-title-${record.id}`} className={labelClass}>
          {copy.publish.cardTitleLabel}
        </label>
        <Input id={`pub-title-${record.id}`} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>

      {/* 文案 */}
      <AiTextField id={`pub-text-${record.id}`} label={copy.publish.cardTextLabel} value={text} onChange={setText} rows={3} />

      {/* 话题 */}
      <div>
        <label htmlFor={`pub-topics-${record.id}`} className={labelClass}>
          {copy.publish.cardTopicsLabel}
        </label>
        <Input id={`pub-topics-${record.id}`} value={topics} onChange={(e) => setTopics(e.target.value)} />
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
        {record.video_url && (
          <a
            href={record.video_url}
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
