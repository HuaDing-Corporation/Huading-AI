"use client";

import { type ReactNode, useEffect, useState } from "react";
import { ChevronDown, Sparkles } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useUploadProductImage } from "@/lib/api/hooks";
import { useGenerateConfirm } from "@/lib/api/use-generate-confirm";
import type { CreateVideoRequest } from "@/lib/api/types";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { AiTextField } from "@/components/workbench/ai-text-field";
import {
  AspectRatioSelect,
  DEFAULT_IMAGE_ASPECT_RATIO,
  IMAGE_ASPECT_RATIOS,
  type ImageAspectRatio
} from "@/components/workbench/aspect-ratio-select";
import { ConfirmGenerateDialog } from "@/components/workbench/confirm-generate-dialog";
import { DEFAULT_IMAGE_RESOLUTION, ImageResolutionPicker, type ImageResolutionTier } from "@/components/workbench/image-resolution-picker";
import { isValidImageCount, ProductImageCountPicker } from "@/components/workbench/product-image-count-picker";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { StrengthSlider } from "@/components/workbench/strength-slider";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { copy } from "@/lib/copy";

// IMAGE-GEN-OPTIMIZE-UI-0001 契约 §四：参考图上限 6（决策 D2）。张数选择器 + 多图 picker 复用电商带货那套（max=per-call）。
const PHOTO_REF_MAX = 6;

// 三个强度（背景参考强度已于 2026-07-19 砍除：BE 盲评无作用、#208 内删除、从未上线）。
const STRENGTH_KEYS = ["similarity_strength", "creativity_strength", "subject_strength"] as const;
type StrengthKey = (typeof STRENGTH_KEYS)[number];
const STRENGTH_META: { key: StrengthKey; label: string; hint: string }[] = [
  { key: "similarity_strength", label: copy.workbench.strengthSimilarity, hint: copy.workbench.strengthSimilarityHint },
  { key: "creativity_strength", label: copy.workbench.strengthCreativity, hint: copy.workbench.strengthCreativityHint },
  { key: "subject_strength", label: copy.workbench.strengthSubject, hint: copy.workbench.strengthSubjectHint }
];
type StrengthState = { enabled: boolean; value: number };
const DEFAULT_STRENGTH: StrengthState = { enabled: false, value: 50 }; // 默认关闭；开启后从 50% 起（十档中位）

const summaryClass =
  "flex cursor-pointer list-none items-center justify-between gap-2 rounded-field px-3.5 py-3 text-[13px] text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold [&::-webkit-details-marker]:hidden";

/** 折叠分组壳（<details> 默认收起）——生成强度 / 任务总控共用（镜像 more-settings 的折叠壳）。 */
function CollapsibleSection({ label, bodyClassName, children }: { label: string; bodyClassName?: string; children: ReactNode }) {
  return (
    <details className="mb-[15px] rounded-field border border-line-gold bg-glass-fill">
      <summary className={summaryClass}>
        {label}
        <ChevronDown size={16} strokeWidth={2} className="text-ink-faint" />
      </summary>
      <div className={`border-t border-line-gold px-3.5 py-3.5 ${bodyClassName ?? ""}`}>{children}</div>
    </details>
  );
}

/**
 * 图片生成 / 修改 (video_mode="photo") workbench container — the third mode. IMAGE-GEN-OPTIMIZE-UI-0001：
 * 参考图单张→1–6 张（复用张数选择器 + ReferenceImagesPicker，注入产品图上传器）；三个强度滑块（各带开关、默认关、
 * 关闭不提交，软性倾向非精确参数）；四层提示词（任务总控/统一负面/图片提示词/图片负面，各层 ≤20000 字符=BE 反滥用上界、超限 422，总控可折叠）。
 * ⚠️ 零回归：AI 封面（cover-panel，video_mode:"photo"+purpose:"cover"+image_size/image_quality）不走本表单、不受影响。
 */
export function PhotoImageForm({
  initialPrompt,
  initialMasterPrompt,
  initialNegativePrompt,
  initialAspectRatio,
  onPrefillConsumed
}: {
  initialPrompt?: string;
  /** 反推带入 · 总控前缀（fill_targets.photo.master_prompt）——REVERSE-DEEP-UI-0001 范围2 */
  initialMasterPrompt?: string;
  /** 反推带入 · 图片负面提示词（fill_targets.photo.negative_prompt → 本表单第 3 层 imageNegative，非 masterNegative） */
  initialNegativePrompt?: string;
  /** 反推带入 · 画面比例（BE 按 D7 已映射为**图片那套**枚举；本表单按自己的枚举常量再兜一道，非法值不落） */
  initialAspectRatio?: string;
  onPrefillConsumed?: () => void;
} = {}) {
  const { createAndTrack } = useVideoTasks();
  const uploadRef = useUploadProductImage();

  // 提示词反推「带入 · 图片生成」注入 prompt(=fill_targets.photo.topic)；惰性消费，mount 后回调 page 清空。
  const [prompt, setPrompt] = useState(() => initialPrompt ?? "");

  // 参考图多图（可选）：张数选择器定上限（默认 1，最多 6），multi picker 上传 → image_keys。
  const [refKeys, setRefKeys] = useState<string[]>([]);
  const [refCount, setRefCount] = useState(1);
  // 四层提示词：图片提示词=prompt（layer3，必填）；下面三层可选。各层 BE 反滥用上界 ≤20000 字符（超限 422，非静默截断）。
  const [masterPrompt, setMasterPrompt] = useState("");
  const [masterNegative, setMasterNegative] = useState("");
  const [imageNegative, setImageNegative] = useState("");
  // 三个强度（各 {enabled,value}，默认关）。
  const [strengths, setStrengths] = useState<Record<StrengthKey, StrengthState>>({
    similarity_strength: { ...DEFAULT_STRENGTH },
    creativity_strength: { ...DEFAULT_STRENGTH },
    subject_strength: { ...DEFAULT_STRENGTH }
  });
  const setStrength = (key: StrengthKey, patch: Partial<StrengthState>) =>
    setStrengths((s) => ({ ...s, [key]: { ...s[key], ...patch } }));

  const [aspectRatio, setAspectRatio] = useState<ImageAspectRatio>(DEFAULT_IMAGE_ASPECT_RATIO); // 默认 1:1
  const [imageResolution, setImageResolution] = useState<ImageResolutionTier>(DEFAULT_IMAGE_RESOLUTION); // §3之二：清晰度档位，默认 1k
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）
  const [error, setError] = useState<string | null>(null);

  /**
   * 提示词反推「带入 · 图片生成」逐字段直落（REVERSE-DEEP-UI-0001 · D3-①）。
   * 🔴 纪律（沿用 page.tsx:57 那段）：**只写 prefill 真正带来的字段** —— 每个 `!== undefined` 各自成门，
   *    BE/弹窗没给的项直接跳过，用户已填的该控件原样保留（**不许用空串覆盖**，那是承重门 2 要钉死的）。
   *    画面比例再按本表单自己的枚举常量兜一道：BE 若给了非图片枚举的值（D7 违约）**宁可不落**，不塞非法值进控件。
   */
  useEffect(() => {
    if (
      initialPrompt === undefined &&
      initialMasterPrompt === undefined &&
      initialNegativePrompt === undefined &&
      initialAspectRatio === undefined
    )
      return;
    if (initialPrompt !== undefined) setPrompt(initialPrompt);
    if (initialMasterPrompt !== undefined) setMasterPrompt(initialMasterPrompt);
    if (initialNegativePrompt !== undefined) setImageNegative(initialNegativePrompt);
    if (initialAspectRatio !== undefined && (IMAGE_ASPECT_RATIOS as readonly string[]).includes(initialAspectRatio))
      setAspectRatio(initialAspectRatio as ImageAspectRatio);
    onPrefillConsumed?.();
  }, [initialPrompt, initialMasterPrompt, initialNegativePrompt, initialAspectRatio, onPrefillConsumed]);

  // Actual submit — runs only after the 确定生成 confirmation; owns its own errors.
  const submit = async (req: CreateVideoRequest) => {
    setError(null);
    try {
      await createAndTrack(req, req.topic ?? "");
    } catch (err) {
      setError(errorText(err));
    }
  };
  const confirm = useGenerateConfirm(submit);

  // 已上传参考图 > 所选张数 → 明确越限（不静默丢图，沿用 ECOM-REF-LIMIT 先例）；仅在张数合法时判。
  const refCountValid = isValidImageCount(refCount, PHOTO_REF_MAX);
  const refOverLimit = refCountValid && refKeys.length > refCount;
  // onGenerate 守卫 与 generateDisabled 共用同一判据，杜绝漂移（uploadRef.isPending 仅在按钮禁用侧、守卫侧不判）。
  const inputInvalid = !prompt.trim() || !refCountValid || refOverLimit;

  const onGenerate = () => {
    if (inputInvalid) return;
    setError(null);
    const trimmed = prompt.trim();
    // 未开启的强度**不出现在提交体**（承重）：只放 enabled 的。
    const enabledStrengths: Partial<Record<StrengthKey, number>> = {};
    for (const k of STRENGTH_KEYS) if (strengths[k].enabled) enabledStrengths[k] = strengths[k].value;
    confirm.requestConfirm({
      topic: trimmed, // 图片提示词（layer3，photo 上限从 2000 放宽到 BE ≤20000；前端不设 maxLength，超 20000 由 BE 422）
      video_mode: "photo",
      ...(refKeys.length > 0 ? { image_keys: refKeys } : {}), // 参考图可选：无则纯文生图，不带 image_keys
      ...(masterPrompt.trim() ? { master_prompt: masterPrompt.trim() } : {}),
      ...(masterNegative.trim() ? { master_negative_prompt: masterNegative.trim() } : {}),
      ...(imageNegative.trim() ? { negative_prompt: imageNegative.trim() } : {}), // 图片负面复用 negative_prompt 字段
      ...enabledStrengths,
      aspect_ratio: aspectRatio, // 画面比例（默认 1:1）；不再带 image_quality/image_size
      image_resolution: imageResolution, // §3之二：清晰度档位，界面选择是硬条件、总随请求传（默认 1k）
      apply_visible_label: applyLabel
    });
  };

  const generateDisabled = uploadRef.isPending || inputInvalid;

  let statusHint: string | null = null;
  if (!prompt.trim()) statusHint = copy.workbench.photoPromptRequired;
  else if (refOverLimit) statusHint = copy.workbench.photoRefImagesExceed(refKeys.length, refCount);

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.photoTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.photoSubtitle}</CardSubtitle>

      {/* layer3 图片提示词（必填） */}
      <AiTextField
        id="photo-prompt"
        label={copy.workbench.photoPromptLabel}
        value={prompt}
        onChange={setPrompt}
        rows={4}
        placeholder={copy.workbench.photoPromptPlaceholder}
      />

      {/* layer4 图片负面提示词（可选） */}
      <AiTextField
        id="photo-negative-prompt"
        label={copy.workbench.imageNegativeLabel}
        value={imageNegative}
        onChange={setImageNegative}
        rows={2}
        placeholder={copy.workbench.imageNegativePlaceholder}
      />

      {/* 参考图（可选 1–6）：张数选择器（上限 6，per-call 不回归电商）+ 多图 picker */}
      <ProductImageCountPicker
        value={refCount}
        onChange={setRefCount}
        max={PHOTO_REF_MAX}
        label={copy.workbench.photoRefCountLabel}
        hint={copy.workbench.photoRefCountHint}
      />
      <ReferenceImagesPicker
        onChange={setRefKeys}
        max={refCountValid ? refCount : PHOTO_REF_MAX}
        inputId="photo-ref"
        label={copy.workbench.photoRefImagesLabel}
        uploadLabel={copy.workbench.photoRefImagesUpload}
        overLimitError={copy.workbench.photoRefImagesOverLimit}
        // 参考图走 /uploads→image_key（与电商产品图同源）：注入上传器复用多图能力。
        uploadFile={(f) => uploadRef.mutateAsync(f).then((r) => r.image_key)}
      />

      <AspectRatioSelect value={aspectRatio} onValueChange={setAspectRatio} />

      {/* §3之二 清晰度档位 1K/2K/4K：与画面比例并列（比例定形状、档位定大小）。 */}
      <ImageResolutionPicker value={imageResolution} onChange={setImageResolution} />

      {/* 生成强度（可选）：三个滑块，各带开关、默认关、关闭不提交。默认收起，避免表单过长。 */}
      <CollapsibleSection label={copy.workbench.strengthGroupLabel}>
        <p className="mb-3 text-[12px] leading-relaxed text-ink-faint">{copy.workbench.strengthGroupHint}</p>
        {STRENGTH_META.map(({ key, label, hint }) => (
          <StrengthSlider
            key={key}
            id={`photo-strength-${key}`}
            label={label}
            hint={hint}
            enabled={strengths[key].enabled}
            value={strengths[key].value}
            onEnabledChange={(enabled) => setStrength(key, { enabled })}
            onValueChange={(value) => setStrength(key, { value })}
          />
        ))}
      </CollapsibleSection>

      {/* layer1+2 任务总控（可选·全局风格）：默认收起 */}
      <CollapsibleSection label={copy.workbench.photoMasterGroupLabel} bodyClassName="flex flex-col gap-1">
        <AiTextField
          id="photo-master-prompt"
          label={copy.workbench.masterPromptLabel}
          value={masterPrompt}
          onChange={setMasterPrompt}
          rows={2}
          placeholder={copy.workbench.masterPromptPlaceholder}
        />
        <AiTextField
          id="photo-master-negative"
          label={copy.workbench.masterNegativeLabel}
          value={masterNegative}
          onChange={setMasterNegative}
          rows={2}
          placeholder={copy.workbench.masterNegativePlaceholder}
        />
      </CollapsibleSection>

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      {statusHint && !error && (
        <p className="mb-3 text-[12.5px] text-ink-soft" aria-live="polite">
          {statusHint}
        </p>
      )}

      <Button variant="primary" size="lg" className="mt-2 w-full" onClick={onGenerate} disabled={generateDisabled}>
        <Sparkles size={18} strokeWidth={1.8} /> {copy.workbench.generatePhoto}
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
