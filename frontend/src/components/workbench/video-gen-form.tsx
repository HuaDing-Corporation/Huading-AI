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
import { ReferenceVideosPicker, type ReferenceVideoItem } from "@/components/workbench/reference-videos-picker";
import { ResolutionPicker } from "@/components/workbench/resolution-picker";
import {
  VideoAspectRatioSelect,
  DEFAULT_VIDEO_ASPECT_RATIO,
  isVideoAspectRatio,
  type VideoAspectRatio
} from "@/components/workbench/video-aspect-ratio-select";
import { BgmPicker } from "@/components/workbench/bgm-picker";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { totalDurationStatus } from "@/lib/media/reference-video";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
// 提示词 2000 字墙（需求2/D4）：BE 对所有非 photo 模式的 topic 判 len>2000→422，而本表单同时发 prompt→topic。
// 前端拦住不发（体验）+ BE 422 friendly 分流（权威）两道都做。用 trim 后长度对齐 BE（先 strip 再判长）。
const PROMPT_MAX = 2000;

/**
 * 视频生成 第6模式（VIDEOGEN-UI-0001 + PARAMS-UI-0001 + V2V-UI-0001）：**参考图或视频（可选，D8 严格二选一互斥）**
 * ——参考图 0–9（BE 0–9，UI 已随需求6 放开可选）/ 参考视频 ≤3 条·合计 1.8–15.2s 联动（D10）+ prompt(≤2000，复用作
 * topic 走 2000 墙) + 负面提示词(可选、不限字数) + 画面比例(7 值，默认自适应——**API 值 `auto`，worker 转为 provider
 * 的 `adaptive`**，#214 P3 订正) + 音频生成开关(默认关) + 时长(预设 5/10/15 + 自定义 4–15) + 分辨率 + BGM(无/上传/库)
 * → POST /videos {video_mode:"video_gen"}，SSE 进度(复用 createAndTrack)。复用 useGenerateConfirm(积分预估随时长 +
 * 防连点)。video_gen 用 prompt(同时作 topic 标题)。ReferenceImagesPicker 默认 max=9 不动（同时服务 batch）。
 */
export function VideoGenForm({
  initialPrompt,
  initialNegativePrompt,
  initialAspectRatio,
  initialDurationSec,
  initialGenerateAudio,
  onPrefillConsumed
}: {
  initialPrompt?: string;
  /** 反推带入 · 负面提示词（fill_targets.video_gen.negative_prompt）——REVERSE-DEEP-UI-0001 范围2 */
  initialNegativePrompt?: string;
  /** 反推带入 · 画面比例（BE 按 D7 已映射为**视频那套**枚举；本表单按自己的枚举常量再兜一道，非法值不落） */
  initialAspectRatio?: string;
  /** 反推带入 · 时长（BE 已按本模块区间 4–15 clamp 并附 duration_clamped 供弹窗提示；此处仍按 isValidDuration 兜一道） */
  initialDurationSec?: number;
  /** 反推带入 · 音频生成开关 */
  initialGenerateAudio?: boolean;
  onPrefillConsumed?: () => void;
} = {}) {
  const { createAndTrack } = useVideoTasks();
  const [refAssetIds, setRefAssetIds] = useState<string[]>([]);
  // 参考视频（V2V-UI-0001，D8 与参考图严格二选一；D10 合计时长联动数据源）。
  const [refVideos, setRefVideos] = useState<ReferenceVideoItem[]>([]);
  // 提示词反推「带入」注入 prompt（同时作 topic）；惰性消费，mount 后回调 page 清空。参考图仍需用户自行上传。
  const [prompt, setPrompt] = useState(() => initialPrompt ?? "");
  const [negativePrompt, setNegativePrompt] = useState(""); // 需求1：负面提示词（可选、不限字数）
  const [durationSec, setDurationSec] = useState<number>(5); // 预设 5/10/15 + 自定义 4–15
  const [resolution, setResolution] = useState<VideoGenResolution>("720p");
  const [aspectRatio, setAspectRatio] = useState<VideoAspectRatio>(DEFAULT_VIDEO_ASPECT_RATIO); // 需求3：默认自适应
  const [generateAudio, setGenerateAudio] = useState(false); // 需求4：音频生成，默认关（零回归）

  /**
   * 提示词反推「带入 · 视频生成」逐字段直落（REVERSE-DEEP-UI-0001 · D3-①）。
   * 🔴 只写 prefill 真正带来的字段（每个 `!== undefined` 各自成门）；没带来的控件**原样保留、不空串覆盖**
   *    （承重门 2 钉的就是这条）。比例/时长再按本表单自己的合法集合兜一道 —— BE 违约给了非法值时**宁可不落**。
   */
  useEffect(() => {
    if (
      initialPrompt === undefined &&
      initialNegativePrompt === undefined &&
      initialAspectRatio === undefined &&
      initialDurationSec === undefined &&
      initialGenerateAudio === undefined
    )
      return;
    if (initialPrompt !== undefined) setPrompt(initialPrompt);
    if (initialNegativePrompt !== undefined) setNegativePrompt(initialNegativePrompt);
    if (initialAspectRatio !== undefined && isVideoAspectRatio(initialAspectRatio)) setAspectRatio(initialAspectRatio);
    if (
      initialDurationSec !== undefined &&
      isValidDuration(initialDurationSec, VIDEO_GEN_DURATION_MIN, VIDEO_GEN_DURATION_MAX)
    )
      setDurationSec(initialDurationSec);
    if (initialGenerateAudio !== undefined) setGenerateAudio(initialGenerateAudio);
    onPrefillConsumed?.();
  }, [
    initialPrompt,
    initialNegativePrompt,
    initialAspectRatio,
    initialDurationSec,
    initialGenerateAudio,
    onPrefillConsumed
  ]);
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
  // D10 合计时长门：有参考视频时合计须 1.8–15.2s（picker 已前置阻断"传了才超"，此为提交侧同源判据兜底）。
  // Code Review：保留三态 status 直接分流（不压平成 bool 再用 15.2 字面量反推——上限只留在 MAX_TOTAL_REFERENCE_SEC 一处）。
  const refVideoTotalSec = refVideos.reduce((s, it) => s + it.duration, 0);
  const refVideoTotalStatus = totalDurationStatus(refVideoTotalSec, refVideos.length);
  // D8 竞态兜底：互斥靠 picker disabled（基于**已完成** items），两侧同时在途上传均落成可绕过 → 提交侧同源兜底拦住。
  const mediaConflict = refAssetIds.length > 0 && refVideos.length > 0;
  // 需求6：参考图/视频均可选（BE refs 0–9；纯文生视频合法）——不再要求 refAssetIds ≥1。
  // onGenerate 守卫 与 generateDisabled 共用同一判据，杜绝漂移。
  const inputInvalid = !promptTrimmed || promptOverLimit || !durationValid || refVideoTotalStatus !== "ok" || mediaConflict;

  const onGenerate = () => {
    if (inputInvalid) return;
    setError(null);
    confirm.requestConfirm({
      topic: promptTrimmed, // video_gen 用 prompt 文本作标题/展示
      prompt: promptTrimmed,
      video_mode: "video_gen",
      // D8 二选一：图与视频互斥，仅带有值的一侧（都空=纯文生视频，两字段均省略）。
      ...(refAssetIds.length > 0 ? { reference_image_asset_ids: refAssetIds } : {}),
      ...(refVideos.length > 0 ? { reference_video_asset_ids: refVideos.map((it) => it.assetId) } : {}),
      duration_sec: durationSec,
      resolution,
      aspect_ratio: aspectRatio, // 需求3：界面选择总随请求传（默认自适应——API 值 `auto`，worker 转 provider 的 `adaptive`，#214 P3）
      generate_audio: generateAudio, // 需求4：布尔总随请求传（默认 false）
      ...(negativePrompt.trim() ? { negative_prompt: negativePrompt.trim() } : {}), // 需求1：可选，空则不带
      bgm, // undefined 时 JSON 序列化自动省略（bgm 可选）
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled = inputInvalid;

  let hint: string | null = null;
  if (!promptTrimmed) hint = copy.workbench.vgPromptRequired;
  else if (mediaConflict) hint = copy.workbench.vgRefMediaExclusiveImages; // 竞态兜底：移除一侧后可生成
  else if (refVideoTotalStatus === "over") hint = copy.workbench.vgRefVideoTotalOver;
  else if (refVideoTotalStatus === "low") hint = copy.workbench.vgRefVideoTotalLow;

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.vgTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.vgSubtitle}</CardSubtitle>

      {/* 需求6「参考图或视频（可选）」：D8 严格二选一——传了视频→图片上传禁用（带原因），反之亦然，UI 直接互斥
          （provider image_with_roles 与 video_urls 不能同用，BE 兜底 422——两道都在）。 */}
      <div className="mb-[15px]">
        <p className={labelClass}>{copy.workbench.vgRefMediaLabel}</p>
        <ReferenceImagesPicker
          onChange={setRefAssetIds}
          disabled={refVideos.length > 0}
          disabledHint={copy.workbench.vgRefMediaExclusiveVideos}
        />
        <ReferenceVideosPicker
          onItemsChange={setRefVideos}
          disabled={refAssetIds.length > 0}
          disabledHint={copy.workbench.vgRefMediaExclusiveImages}
        />
      </div>

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
      {/* 出片慢预期管理（FIX1 §4：SPIKE 实测 233–329s）——常驻小字，用户知道等几分钟是正常的 */}
      <p className="mt-2 text-center text-[12px] text-ink-faint">{copy.workbench.vgSlowHint}</p>

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
