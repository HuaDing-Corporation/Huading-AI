"use client";

import { useEffect, useState } from "react";
import { Clapperboard } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import type { CreateVideoRequest, VideoGenBgm, VideoGenResolution } from "@/lib/api/types";
import { VIDEO_GEN_DURATIONS, VIDEO_GEN_DURATION_MAX, VIDEO_GEN_DURATION_MIN } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { DurationPicker, isValidDuration } from "@/components/workbench/duration-picker";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { ResolutionPicker } from "@/components/workbench/resolution-picker";
import { VideoAspectRatioSelect, DEFAULT_VIDEO_ASPECT_RATIO, type VideoAspectRatio } from "@/components/workbench/video-aspect-ratio-select";
import { BgmPicker } from "@/components/workbench/bgm-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
// 提示词 2000 字墙（需求2/D4）：BE 对所有非 photo 模式的 topic 判 len>2000→422，而本表单同时发 prompt→topic。
// 前端拦住不发（体验）+ BE 422 friendly 分流（权威）两道都做。用 trim 后长度对齐 BE（先 strip 再判长）。
const PROMPT_MAX = 2000;

/**
 * 视频生成 第6模式（VIDEOGEN-UI-0001 + VIDEO-GEN-PARAMS-UI-0001）：多参考图(≤9) + prompt(≤2000，复用作 topic 走 2000 墙) +
 * 负面提示词(可选、不限字数) + 画面比例(7 值，默认 adaptive) + 音频生成开关(默认关) + 时长(预设 5/10/15 + 自定义 4–15) +
 * 分辨率(480p/720p/1080p) + BGM(无/上传/库) → POST /videos {video_mode:"video_gen"}，SSE 进度(复用 createAndTrack)。
 * 复用 useGenerateConfirm/ConfirmGenerateDialog(积分预估随时长 + 防连点)。video_gen 用 prompt(同时作 topic 标题)。
 * ⚠️ 需求6（参考图或视频上传）按 D7 单独拆批，不在本包；ReferenceImagesPicker 默认 max=9 不动（同时服务 batch）。
 */
export function VideoGenForm({
  initialPrompt,
  onPrefillConsumed
}: { initialPrompt?: string; onPrefillConsumed?: () => void } = {}) {
  const { createAndTrack } = useVideoTasks();
  const [refAssetIds, setRefAssetIds] = useState<string[]>([]);
  // 提示词反推「带入」注入 prompt（同时作 topic）；惰性消费，mount 后回调 page 清空。参考图仍需用户自行上传。
  const [prompt, setPrompt] = useState(() => initialPrompt ?? "");
  useEffect(() => {
    if (initialPrompt === undefined) return;
    setPrompt(initialPrompt);
    onPrefillConsumed?.();
  }, [initialPrompt, onPrefillConsumed]);
  const [negativePrompt, setNegativePrompt] = useState(""); // 需求1：负面提示词（可选、不限字数）
  const [durationSec, setDurationSec] = useState<number>(5); // 预设 5/10/15 + 自定义 4–15
  const [resolution, setResolution] = useState<VideoGenResolution>("720p");
  const [aspectRatio, setAspectRatio] = useState<VideoAspectRatio>(DEFAULT_VIDEO_ASPECT_RATIO); // 需求3：默认自适应
  const [generateAudio, setGenerateAudio] = useState(false); // 需求4：音频生成，默认关（零回归）
  const [bgm, setBgm] = useState<VideoGenBgm | undefined>(undefined);
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);

  // 真正提交（仅「确定生成」后）；自管错误。createAndTrack 第二参为展示标题=prompt。
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      await createAndTrack(req, req.topic ?? "");
    } catch (err) {
      setError(errorText(err));
    }
  };
  const confirm = useGenerateConfirm(submit);

  const promptTrimmed = prompt.trim();
  // 码点计数对齐 BE（Code Review：BE 用 Python len()=码点，JS .length=UTF-16 码元会把增补面 emoji 记 2 → 误杀 1001 个 emoji）。
  const promptCharCount = [...promptTrimmed].length;
  const promptOverLimit = promptCharCount > PROMPT_MAX; // 需求2：超 2000 → 红字 + 拦住
  const durationValid = isValidDuration(durationSec, VIDEO_GEN_DURATION_MIN, VIDEO_GEN_DURATION_MAX); // 需求5：整数 4–15
  // onGenerate 守卫 与 generateDisabled 共用同一判据，杜绝漂移。
  const inputInvalid = refAssetIds.length < 1 || !promptTrimmed || promptOverLimit || !durationValid;

  const onGenerate = () => {
    if (inputInvalid) return;
    setError(null);
    confirm.requestConfirm({
      topic: promptTrimmed, // video_gen 用 prompt 文本作标题/展示
      prompt: promptTrimmed,
      video_mode: "video_gen",
      reference_image_asset_ids: refAssetIds,
      duration_sec: durationSec,
      resolution,
      aspect_ratio: aspectRatio, // 需求3：界面选择总随请求传（默认 adaptive）
      generate_audio: generateAudio, // 需求4：布尔总随请求传（默认 false）
      ...(negativePrompt.trim() ? { negative_prompt: negativePrompt.trim() } : {}), // 需求1：可选，空则不带
      bgm, // undefined 时 JSON 序列化自动省略（bgm 可选）
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled = inputInvalid;

  let hint: string | null = null;
  if (refAssetIds.length < 1) hint = copy.workbench.vgRefImagesRequired;
  else if (!promptTrimmed) hint = copy.workbench.vgPromptRequired;

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.vgTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.vgSubtitle}</CardSubtitle>

      <ReferenceImagesPicker onChange={setRefAssetIds} />

      {/* Code Review a11y：计数不进 live region（免读屏冗余播报）；超限用 role=alert 出现即播报一次 + 持久存在，
          经 aria-describedby 回连 textarea（聚焦可复述原因）+ aria-invalid（invalid 态）。超限文案=用户原话 + 括号附实际计数
          （用户需知道超了多少才能删）。 */}
      <AiTextField
        id="vg-prompt"
        label={copy.workbench.vgPromptLabel}
        value={prompt}
        onChange={setPrompt}
        rows={4}
        placeholder={copy.workbench.vgPromptPlaceholder}
        ariaInvalid={promptOverLimit}
        ariaDescribedby="vg-prompt-limit"
        footer={
          promptOverLimit ? (
            <span id="vg-prompt-limit" role="alert" className="font-medium text-error-fg">
              {copy.workbench.vgPromptOverLimit}（{promptCharCount} / {PROMPT_MAX}）
            </span>
          ) : (
            <span id="vg-prompt-limit" className="text-ink-faint">
              {promptCharCount} / {PROMPT_MAX}
            </span>
          )
        }
      />

      {/* 负面提示词（需求1，可选、不限字数） */}
      <AiTextField
        id="vg-negative"
        label={copy.workbench.vgNegativeLabel}
        value={negativePrompt}
        onChange={setNegativePrompt}
        rows={2}
        placeholder={copy.workbench.vgNegativePlaceholder}
      />

      {/* 时长：预设 5/10/15 + 自定义 4–15（复用参数化 DurationPicker，越界红字拦截） */}
      <DurationPicker
        value={durationSec}
        onChange={setDurationSec}
        label={copy.workbench.vgDurationLabel}
        presets={VIDEO_GEN_DURATIONS}
        min={VIDEO_GEN_DURATION_MIN}
        max={VIDEO_GEN_DURATION_MAX}
      />

      {/* 画面比例（需求3，7 值，默认自适应；显式比例旁裁切提示） */}
      <VideoAspectRatioSelect value={aspectRatio} onValueChange={setAspectRatio} />

      {/* 分辨率 480p/720p/1080p（共享 ResolutionPicker，电商带货同款） */}
      <ResolutionPicker value={resolution} onChange={setResolution} />

      {/* 音频生成开关（需求4，默认关）——紧邻 BGM，文案区分「模型生成音频」vs「另配 BGM 混音」 */}
      <div className="mb-[15px]">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="vg-audio" className={labelClass}>
            {copy.workbench.vgAudioLabel}
          </label>
          <Switch
            id="vg-audio"
            checked={generateAudio}
            onCheckedChange={setGenerateAudio}
            ariaLabel={copy.workbench.vgAudioToggleAria}
            ariaDescribedby="vg-audio-hint"
          />
        </div>
        <p id="vg-audio-hint" className="mt-1.5 text-[12px] text-ink-faint">
          {copy.workbench.vgAudioHint}
        </p>
      </div>

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
