"use client";

import { useEffect, useState } from "react";
import { Download, Image as ImageIcon, Scissors, Sparkles } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { errorText } from "@/lib/api/error-text";
import { useCoverFromFrame, useFrameCandidates } from "@/lib/api/hooks";
import { friendlyImageError } from "@/lib/api/image-error";
import { videoKeys } from "@/lib/api/keys";
import type { Cover, SubtitlePosition } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { Tabs, TabsContent, TabsList, TabsTrigger, tabTriggerClass } from "@/components/ui/tabs";
import { TextStyleControls } from "@/components/ui/text-style-controls";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const TITLE_FONT_MIN = 24;
const TITLE_FONT_MAX = 120;

function titleFontValid(n: number): boolean {
  return Number.isFinite(n) && n >= TITLE_FONT_MIN && n <= TITLE_FONT_MAX;
}

/** 封面预览 + 下载 + 已存历史提示（截帧 / AI 共用）。 */
function CoverResult({ imageUrl, downloadUrl }: { imageUrl: string; downloadUrl?: string | null }) {
  return (
    <div className="mt-4 flex flex-col gap-2">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageUrl}
        alt={copy.cover.resultAlt}
        className="w-full rounded-field border border-line-gold bg-black/5 object-contain"
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] text-ink-faint">{copy.cover.savedToHistory}</span>
        <a
          href={downloadUrl ?? imageUrl}
          download
          className="inline-flex items-center gap-1.5 rounded-field border border-line-gold bg-glass-fill px-3 py-1.5 text-[13px] text-gold-deep transition-colors hover:bg-glass-hover"
        >
          <Download size={15} strokeWidth={2} /> {copy.cover.download}
        </a>
      </div>
    </div>
  );
}

/** 截帧 tab：候选帧选择 + 标题叠加 → POST /covers/from-frame。Exported 供单测(避开 jsdom Radix tab)。 */
export function FrameCoverTab({ videoTaskId }: { videoTaskId: string }) {
  const frames = useFrameCandidates(videoTaskId);
  const coverGen = useCoverFromFrame();

  const [timestamp, setTimestamp] = useState<number | null>(null);
  const [titleText, setTitleText] = useState("");
  const [fontSize, setFontSize] = useState(64);
  const [color, setColor] = useState("#FFFFFF");
  const [position, setPosition] = useState<SubtitlePosition>("bottom");
  const [cover, setCover] = useState<Cover | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 选帧/标题/样式变更 → 清掉上一次生成的封面预览，避免陈旧态误导（Code Review）。
  useEffect(() => {
    setCover(null);
  }, [timestamp, titleText, fontSize, color, position]);

  const fontError = !titleFontValid(fontSize);
  const generateDisabled = timestamp === null || coverGen.isPending || fontError;

  const onGenerate = async () => {
    if (timestamp === null) return;
    setError(null);
    try {
      const res = await coverGen.mutateAsync({
        video_task_id: videoTaskId,
        timestamp_sec: timestamp,
        // 标题可空 → text "" → 后端纯截帧不叠字
        title: { text: titleText.trim(), font_size: fontSize, color, position }
      });
      setCover(res.cover);
    } catch (err) {
      setError(errorText(err));
    }
  };

  if (frames.isLoading) {
    return <p className="py-10 text-center text-[13px] text-ink-soft">{copy.cover.frameLoading}</p>;
  }
  if (frames.isError) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center">
        <p className="text-[13px] text-error-fg">{copy.cover.frameError}</p>
        <Button variant="soft" size="sm" onClick={() => void frames.refetch()}>
          {copy.cover.retry}
        </Button>
      </div>
    );
  }
  const candidates = frames.data ?? [];
  if (candidates.length === 0) {
    return <p className="py-10 text-center text-[13px] text-ink-soft">{copy.cover.frameEmpty}</p>;
  }

  return (
    <div>
      <label className={labelClass}>{copy.cover.frameLabel}</label>
      <div className="mb-[15px] grid grid-cols-3 gap-2">
        {candidates.map((f) => (
          <SelectableOption
            key={f.timestamp_sec}
            selected={timestamp === f.timestamp_sec}
            onSelect={() => setTimestamp(f.timestamp_sec)}
            className="flex-col items-stretch gap-1 p-1.5"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={f.preview_url} alt="" className="aspect-video w-full rounded-mark object-cover" />
            <span className="text-center text-[11.5px] text-ink-faint">{f.timestamp_sec.toFixed(1)}s</span>
          </SelectableOption>
        ))}
      </div>

      <div className="mb-[15px]">
        <label htmlFor="cover-title" className={labelClass}>
          {copy.cover.titleLabel}
        </label>
        <Input
          id="cover-title"
          value={titleText}
          onChange={(e) => setTitleText(e.target.value)}
          placeholder={copy.cover.titlePlaceholder}
        />
      </div>

      <div className="mb-[15px] flex flex-col gap-[15px]">
        <TextStyleControls
          idPrefix="cover-title"
          fontSizeLabel={copy.cover.titleFontSizeLabel}
          colorLabel={copy.cover.titleColorLabel}
          positionLabel={copy.cover.titlePositionLabel}
          fontMin={TITLE_FONT_MIN}
          fontMax={TITLE_FONT_MAX}
          fontSize={fontSize}
          onFontSizeChange={setFontSize}
          fontError={fontError}
          fontErrorMessage={
            fontError ? (
              <p role="alert" className="text-[12.5px] text-error-fg">
                {copy.cover.titleFontSizeRange}
              </p>
            ) : undefined
          }
          color={color}
          onColorChange={setColor}
          position={position}
          onPositionChange={setPosition}
        />
      </div>

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="w-full" onClick={() => void onGenerate()} disabled={generateDisabled}>
        <Scissors size={18} strokeWidth={1.8} /> {coverGen.isPending ? copy.cover.generatingFrame : copy.cover.generateFrame}
      </Button>

      {cover && <CoverResult imageUrl={cover.image_url} />}
    </div>
  );
}

/** AI tab：prompt → 复用 0003 文生图(purpose=cover)→ 经 tasks-context 轮询 → 预览。Exported 供单测。 */
export function AiCoverTab() {
  const { createAndTrack, tasks } = useVideoTasks();
  const qc = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [coverTaskId, setCoverTaskId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const coverTask = coverTaskId ? tasks.find((t) => t.taskId === coverTaskId) : undefined;
  const done = coverTask?.status === "done";
  const failed = coverTask?.status === "failed";
  const pending = submitting || (!!coverTask && !done && !failed);
  const generateDisabled = !prompt.trim() || pending;

  // AI 封面完成 → 失效图片历史(photo,含 kind=cover 前缀)，与截帧 useCoverFromFrame 刷新行为对齐。
  useEffect(() => {
    if (done) qc.invalidateQueries({ queryKey: [...videoKeys.all, "history", "photo"] });
  }, [done, qc]);

  const onGenerate = async () => {
    const p = prompt.trim();
    if (!p || pending) return;
    setError(null);
    setSubmitting(true);
    try {
      const id = await createAndTrack(
        { topic: p, video_mode: "photo", purpose: "cover", image_size: "1024x1536", image_quality: "medium" },
        p
      );
      setCoverTaskId(id);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <AiTextField
        id="cover-ai-prompt"
        label={copy.cover.aiPromptLabel}
        value={prompt}
        onChange={setPrompt}
        rows={3}
        placeholder={copy.cover.aiPromptPlaceholder}
      />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="w-full" onClick={() => void onGenerate()} disabled={generateDisabled}>
        <Sparkles size={18} strokeWidth={1.8} /> {pending ? copy.cover.generatingAi : copy.cover.generateAi}
      </Button>

      {pending && (
        <p className="mt-3 text-center text-[13px] text-ink-soft" aria-live="polite">
          {coverTask ? coverTask.statusLabel : copy.cover.aiPending}
        </p>
      )}
      {failed && (
        <p role="alert" className="mt-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {friendlyImageError(coverTask?.errorCode)}
        </p>
      )}
      {done && coverTask?.playbackUrl && (
        <CoverResult imageUrl={coverTask.playbackUrl} downloadUrl={coverTask.downloadUrl} />
      )}
      {done && !coverTask?.playbackUrl && (
        <p role="status" aria-live="polite" className="mt-3 text-center text-[13px] text-ink-soft">
          {copy.cover.aiDoneNoUrl}
        </p>
      )}
    </div>
  );
}

export interface CoverPanelProps {
  videoTaskId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * 封面面板(口播视频产物附属，ORAL-PROD-UI-0001) — Dialog 弹层，双 tab 二选一：
 * 截帧(候选帧 + 标题叠加 → /covers/from-frame) / AI 封面(复用 0003 文生图 purpose=cover)。
 * 封面产物均入图片历史(kind=cover)。非独立工作台模式。
 */
export function CoverPanel({ videoTaskId, open, onOpenChange }: CoverPanelProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] w-[min(94vw,640px)] overflow-y-auto">
        <DialogTitle className="text-base font-semibold text-ink">{copy.cover.title}</DialogTitle>
        <p className="mb-4 mt-1 text-[13px] text-ink-soft">{copy.cover.subtitle}</p>

        <Tabs defaultValue="frame">
          <TabsList className="mb-4 flex gap-1.5 rounded-pill border border-line-gold bg-glass-soft p-1">
            <TabsTrigger value="frame" className={tabTriggerClass}>
              <Scissors size={14} strokeWidth={1.8} /> {copy.cover.tabFrame}
            </TabsTrigger>
            <TabsTrigger value="ai" className={tabTriggerClass}>
              <ImageIcon size={14} strokeWidth={1.8} /> {copy.cover.tabAi}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="frame" className="outline-none">
            <FrameCoverTab videoTaskId={videoTaskId} />
          </TabsContent>
          <TabsContent value="ai" className="outline-none">
            <AiCoverTab />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
