"use client";

import { useState } from "react";
import { Sparkles } from "lucide-react";

import { useModelBatch, useModelImage, useModelStyles } from "@/lib/api/hooks";
import type { ModelGender } from "@/lib/api/types";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { SelectableOption } from "@/components/ui/selectable-option";
import { AspectRatioSelect, DEFAULT_IMAGE_ASPECT_RATIO, type ImageAspectRatio } from "@/components/workbench/aspect-ratio-select";
import { EcomImageTool } from "@/components/workbench/ecom-image-tool";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MAX_CUSTOM = 200;

const GENDER_OPTIONS: { id: ModelGender; label: string }[] = [
  { id: "female", label: copy.workbench.ecomGenderFemale },
  { id: "male", label: copy.workbench.ecomGenderMale },
  { id: "any", label: copy.workbench.ecomGenderAny }
];

/**
 * 电商图 · AI 模特(Phase2) —— 复用 EcomImageTool 外壳，提供「性别 + 风格预设 + 自定义补充」
 * 偏好 + model 提交 + 合规提示。风格预设来自 GET /ecom-images/model-styles（必选才可生成）。
 * 单张 POST /ecom-images/model、批量 /model/batch 返回已创建 photo task(kind=ecom_model)，
 * 由外壳 trackExisting 轮询（产物进 TaskList + 图片历史）。
 */
export function EcomImageModelForm({ initialCustom }: { initialCustom?: string } = {}) {
  const model = useModelImage();
  const modelBatch = useModelBatch();
  const styles = useModelStyles();

  const [gender, setGender] = useState<ModelGender>("female");
  const [styleId, setStyleId] = useState<string | null>(null);
  const [aspectRatio, setAspectRatio] = useState<ImageAspectRatio>(DEFAULT_IMAGE_ASPECT_RATIO); // 画面比例，默认 1:1
  // 提示词反推「带入 · 电商图(AI 模特)」惰性注入自定义补充(= extra_prompt)。
  const [custom, setCustom] = useState(() => (initialCustom ?? "").slice(0, MAX_CUSTOM));

  const styleList = styles.data ?? [];
  const extraPrompt = custom.trim() || undefined;

  return (
    <EcomImageTool
      title={copy.workbench.ecomModelTitle}
      subtitle={copy.workbench.ecomModelSubtitle}
      idPrefix="ecom-model"
      icon={<Sparkles size={18} strokeWidth={1.8} />}
      submitting={model.isPending || modelBatch.isPending}
      extraValid={!!styleId}
      complianceHint={copy.workbench.ecomModelCompliance}
      onSubmitSingle={async (assetId, applyVisibleLabel) => {
        const res = await model.mutateAsync({
          source_asset_id: assetId,
          gender,
          style_id: styleId as string,
          extra_prompt: extraPrompt,
          aspect_ratio: aspectRatio,
          apply_visible_label: applyVisibleLabel
        });
        return [res.task_id];
      }}
      onSubmitBatch={async (assetIds, applyVisibleLabel) => {
        const res = await modelBatch.mutateAsync({
          items: assetIds.map((id) => ({ source_asset_id: id, gender, style_id: styleId as string, extra_prompt: extraPrompt, aspect_ratio: aspectRatio, apply_visible_label: applyVisibleLabel }))
        });
        return res.tasks.map((t) => t.task_id);
      }}
    >
      {/* 模特性别 */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.ecomGenderLabel}</legend>
        <div className="grid grid-cols-3 gap-2">
          {GENDER_OPTIONS.map(({ id, label }) => (
            <SelectableOption key={id} selected={gender === id} onSelect={() => setGender(id)} className="justify-center">
              {label}
            </SelectableOption>
          ))}
        </div>
      </fieldset>

      {/* 风格预设（必选） */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.ecomStyleLabel}</legend>
        {styles.isLoading ? (
          <p className="text-[12.5px] text-ink-soft" aria-live="polite">
            {copy.workbench.ecomStyleLoading}
          </p>
        ) : styles.isError ? (
          <p role="alert" className="text-[12.5px] text-error-fg">
            {copy.workbench.ecomStyleError}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {styleList.map((s) => (
              <SelectableOption key={s.id} selected={styleId === s.id} onSelect={() => setStyleId(s.id)} className="justify-center">
                {s.name}
              </SelectableOption>
            ))}
          </div>
        )}
      </fieldset>

      {/* 自定义补充（可选，≤200） */}
      <AiTextField
        id="ecom-model-custom"
        label={copy.workbench.ecomCustomLabel}
        value={custom}
        onChange={(v) => setCustom(v.slice(0, MAX_CUSTOM))}
        rows={2}
        placeholder={copy.workbench.ecomCustomPlaceholder}
        footer={`${custom.length}/${MAX_CUSTOM}`}
      />

      {/* 画面比例（默认 1:1） */}
      <AspectRatioSelect value={aspectRatio} onValueChange={setAspectRatio} />
    </EcomImageTool>
  );
}
