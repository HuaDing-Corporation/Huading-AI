"use client";

import { useEffect, useRef, useState } from "react";
import { Clapperboard } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import type { CreateVideoRequest, VideoGenBgm, VideoGenDuration, VideoGenResolution } from "@/lib/api/types";
import { VIDEO_GEN_DURATIONS } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { SelectableOption } from "@/components/ui/selectable-option";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { ResolutionPicker } from "@/components/workbench/resolution-picker";
import { BgmPicker } from "@/components/workbench/bgm-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 视频生成 第6模式（VIDEOGEN-UI-0001，seam §5）：多参考图(≤9) + 不限 prompt + 时长(5/10/15) +
 * 分辨率(480p/720p/1080p,默认720p) + BGM(无/上传/库) → POST /videos {video_mode:"video_gen"}，SSE 进度(复用
 * createAndTrack)，出片入历史。复用 useGenerateConfirm/ConfirmGenerateDialog(积分预估 + 防连点)。
 * video_gen 用 prompt(同时作 topic 标题)；其余模式不受影响。
 */
export function VideoGenForm({
  initialPrompt,
  onPrefillConsumed
}: { initialPrompt?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const [refAssetIds, setRefAssetIds] = useState<string[]>([]);
  // 提示词反推「带入」注入 prompt（同时作 topic）；惰性消费，mount 后回调 page 清空。参考图仍需用户自行上传。
  const [prompt, setPrompt] = useState(() => initialPrompt ?? "");
  const prefillConsumed = useRef(false);
  useEffect(() => {
    if (!prefillConsumed.current && initialPrompt !== undefined) {
      prefillConsumed.current = true;
      onPrefillConsumed?.();
    }
  }, [initialPrompt, onPrefillConsumed]);
  const [durationSec, setDurationSec] = useState<VideoGenDuration>(5);
  const [resolution, setResolution] = useState<VideoGenResolution>("720p");
  const [bgm, setBgm] = useState<VideoGenBgm | undefined>(undefined);
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);

  // 真正提交（仅「确定生成」后）；自管错误。createAndTrack 第二参为展示标题=prompt。
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      await createAndTrack(req, req.topic);
    } catch (err) {
      setError(errorText(err));
    }
  };
  const confirm = useGenerateConfirm(submit);

  // 校验后开确认窗（对齐 seam：参考图 1–9、prompt 非空、duration 枚举、resolution 枚举）。
  const onGenerate = () => {
    const trimmed = prompt.trim();
    if (refAssetIds.length < 1 || !trimmed) return;
    setError(null);
    confirm.requestConfirm({
      topic: trimmed, // video_gen 用 prompt 文本作标题/展示
      prompt: trimmed,
      video_mode: "video_gen",
      reference_image_asset_ids: refAssetIds,
      duration_sec: durationSec,
      resolution,
      bgm, // undefined 时 JSON 序列化自动省略（bgm 可选）
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled = refAssetIds.length < 1 || !prompt.trim();

  let hint: string | null = null;
  if (refAssetIds.length < 1) hint = copy.workbench.vgRefImagesRequired;
  else if (!prompt.trim()) hint = copy.workbench.vgPromptRequired;

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.vgTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.vgSubtitle}</CardSubtitle>

      <ReferenceImagesPicker onChange={setRefAssetIds} />

      <AiTextField
        id="vg-prompt"
        label={copy.workbench.vgPromptLabel}
        value={prompt}
        onChange={setPrompt}
        rows={4}
        placeholder={copy.workbench.vgPromptPlaceholder}
      />

      {/* 时长档 5/10/15 */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.vgDurationLabel}</legend>
        <div className="grid grid-cols-3 gap-2">
          {VIDEO_GEN_DURATIONS.map((sec) => (
            <SelectableOption key={sec} selected={durationSec === sec} onSelect={() => setDurationSec(sec)} className="justify-center">
              {copy.workbench.durationSeconds(sec)}
            </SelectableOption>
          ))}
        </div>
      </fieldset>

      {/* 分辨率 480p/720p/1080p（共享 ResolutionPicker，电商带货同款） */}
      <ResolutionPicker value={resolution} onChange={setResolution} />

      <BgmPicker onChange={setBgm} />

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}
      {hint && !error && (
        <p className="mb-3 text-[12.5px] text-ink-soft" aria-live="polite">
          {hint}
        </p>
      )}

      <Button variant="primary" size="lg" className="mt-2 w-full" onClick={onGenerate} disabled={generateDisabled}>
        <Clapperboard size={18} strokeWidth={1.8} /> {copy.workbench.generate}
      </Button>

      <ConfirmGenerateDialog
        open={confirm.open}
        request={confirm.request}
        submitting={confirm.submitting}
        onConfirm={confirm.confirm}
        onCancel={confirm.cancel}
      />
    </Card>
  );
}
