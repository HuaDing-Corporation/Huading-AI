"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";

import { useModelImage, useModelStyles } from "@/lib/api/hooks";
import { useVideoTasks } from "@/lib/videos/tasks-context";
import { useLabelTogglePreference } from "@/lib/preferences/label-toggle";
import { errorText } from "@/lib/api/error-text";
import type { ModelGender, ProductImagesMode } from "@/lib/api/types";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { SelectableOption } from "@/components/ui/selectable-option";
import {
  AspectRatioSelect,
  DEFAULT_IMAGE_ASPECT_RATIO,
  IMAGE_ASPECT_RATIOS,
  type ImageAspectRatio
} from "@/components/workbench/aspect-ratio-select";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { ResultTile } from "@/components/workbench/ecom-image-tool";
import { AiLabelToggle } from "@/components/label/ai-label-toggle";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
// 商品图 + 模特图 合计上限（D1）——对齐后端保守守卫（apimart.py:243-244 的 6 张）。
const IMAGE_BUDGET = 6;

const GENDER_OPTIONS: { id: ModelGender; label: string }[] = [
  { id: "female", label: copy.workbench.ecomGenderFemale },
  { id: "male", label: copy.workbench.ecomGenderMale },
  { id: "any", label: copy.workbench.ecomGenderAny }
];

/**
 * 电商图 · AI 模特（ECOM-MODEL-OPTIMIZE-UI-0001）——**独立单任务表单**（不再走 EcomImageTool 外壳的
 * 单/批标量上传：新契约「1 任务 = 多商品图 + 多模特图」与外壳的单源/批量 fan-out 语义不兼容，外壳留给白底图）。
 * 复用**机制部件而非重造**：ReferenceImagesPicker×2（商品图/模特图，动态 max 实现合计≤6 联动）、ResultTile、
 * trackExisting、AiLabelToggle。
 *   D1 商品图 1–N + 模特图 0–N，合计 ≤6（剩余额度 live 播报，超限由 picker 明确阻断不静默）；
 *   D2 商品图组合语义 product_images_mode（默认 multi_item，>1 张才显开关）；
 *   D3 风格预设改可选 + 自定义风格（与预设 UI 互斥）；D4 自定义补充取消旧 200 截断（BE ≤20000 反滥用上界，超限 422）。
 * 提交 POST /ecom-images/model（product_asset_ids/model_asset_ids/product_images_mode/style_id?/custom_style?），
 * 返回 photo task(kind=ecom_model)，由 tasks-context.trackExisting 轮询（产物进 TaskList + 图片历史）。
 */
export function EcomImageModelForm({
  initialCustom,
  initialAspectRatio,
  onPrefillConsumed
}: {
  initialCustom?: string;
  /** 反推带入 · 画面比例（fill_targets.ecom_model.aspect_ratio）——REVERSE-DEEP-UI-0001 范围2；按图片枚举兜一道 */
  initialAspectRatio?: string;
  onPrefillConsumed?: () => void;
} = {}) {
  const model = useModelImage();
  const styles = useModelStyles();
  const { tasks, trackExisting } = useVideoTasks();
  const [applyLabel, setApplyLabel] = useLabelTogglePreference(); // AI 标识开关（默认关，localStorage 记忆）

  const [gender, setGender] = useState<ModelGender>("female");
  const [styleId, setStyleId] = useState<string | null>(null);
  const [customStyle, setCustomStyle] = useState(""); // 自定义风格（与 styleId 互斥）
  const [productImagesMode, setProductImagesMode] = useState<ProductImagesMode>("multi_item"); // D2 默认多件搭配
  const [aspectRatio, setAspectRatio] = useState<ImageAspectRatio>(DEFAULT_IMAGE_ASPECT_RATIO); // 画面比例，默认 1:1
  const [custom, setCustom] = useState(initialCustom ?? ""); // 自定义补充（D4：取消旧 200 截断、无 slice；BE ≤20000 上界，超限 422）
  const [productIds, setProductIds] = useState<string[]>([]);
  const [modelIds, setModelIds] = useState<string[]>([]);
  const [submittedId, setSubmittedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 提示词反推「带入 · 电商图(AI 模特)」：注入自定义补充（= extra_prompt）+ 画面比例（REVERSE-DEEP-UI-0001 范围2）。
  // D4：取消 200 截断，直接注入全文。纪律：每个 `!== undefined` 各自成门，只写 prefill 真带来的字段；
  // 比例按本表单自己的图片枚举兜一道（BE 违约给了非法值宁可不落）。
  useEffect(() => {
    if (initialCustom === undefined && initialAspectRatio === undefined) return;
    if (initialCustom !== undefined) setCustom(initialCustom);
    if (initialAspectRatio !== undefined && (IMAGE_ASPECT_RATIOS as readonly string[]).includes(initialAspectRatio))
      setAspectRatio(initialAspectRatio as ImageAspectRatio);
    onPrefillConsumed?.();
  }, [initialCustom, initialAspectRatio, onPrefillConsumed]);

  const styleList = styles.data ?? [];
  const productCount = productIds.length;
  const modelCount = modelIds.length;
  const remaining = IMAGE_BUDGET - productCount - modelCount; // D1 剩余额度（合计 ≤6）
  // 🔴 合计超限的**合并客户端守卫**（D1）：两 picker 的动态 max 各自捕获渲染时的 max，**并发跨 picker 上传**
  //   （一边多张大图仍在途、另一边又选图）会各按 max=6 放行 → 可越到 7。picker 是常见（顺序）路径的一道闸，
  //   这里的合并校验 + 生成前 disable 是并发竞态的兜底闸（连同 mock/BE 的合计>6→422，杜绝越限请求，绝不静默）。
  const overBudget = productCount + modelCount > IMAGE_BUDGET;
  const customFilled = customStyle.trim().length > 0; // 自定义风格已填 → 预设禁用（D3 互斥）

  const onGenerate = async () => {
    setError(null);
    // 商品图至少 1 张（D1）；模特图/风格均可选。正常 UI 已 disabled，这里防 fireEvent/快速绕过。
    if (productCount < 1) {
      setError(copy.workbench.ecomModelProductRequired);
      return;
    }
    if (overBudget) {
      setError(copy.workbench.ecomModelOverLimit); // 并发上传越限的兜底：不发越限请求，明确提示移除
      return;
    }
    try {
      const res = await model.mutateAsync({
        product_asset_ids: productIds,
        model_asset_ids: modelCount ? modelIds : undefined,
        // 组合语义仅在 >1 张商品图时有意义（开关也仅此时显示）；≤1 张一律回默认 multi_item，与「默认 multi_item」一致。
        product_images_mode: productCount > 1 ? productImagesMode : "multi_item",
        gender,
        // D3 互斥：填了自定义风格 → 只发 custom_style（不发 style_id）；否则发所选预设（可无）。
        style_id: customFilled ? undefined : styleId ?? undefined,
        custom_style: customFilled ? customStyle.trim() : undefined,
        extra_prompt: custom.trim() || undefined,
        aspect_ratio: aspectRatio,
        apply_visible_label: applyLabel
      });
      trackExisting(res.task_id, copy.workbench.ecomModelTitle, "photo", applyLabel);
      setSubmittedId(res.task_id);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const generateDisabled = model.isPending || productCount < 1 || overBudget;
  const resultTask = submittedId ? tasks.find((t) => t.taskId === submittedId) : undefined;

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.ecomModelTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.ecomModelSubtitle}</CardSubtitle>

      {/* 剩余额度（D1）：合计 ≤6 联动，对屏幕阅读器 live 播报额度变化；越限（并发上传竞态）→ 转红色警示 + role=alert */}
      {overBudget ? (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {copy.workbench.ecomModelOverLimit}
        </p>
      ) : (
        <p aria-live="polite" className="mb-3 rounded-field bg-glass-soft px-3 py-2 text-[12.5px] text-ink-soft">
          {remaining > 0 ? copy.workbench.ecomModelBudgetRemaining(remaining) : copy.workbench.ecomModelBudgetFull}
        </p>
      )}

      {/* 商品图组合方式（D2）：仅 >1 张商品图时出现（单张无意义 → 渐进披露）；始终随请求提交 product_images_mode */}
      {productCount > 1 && (
        <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
          <legend className={labelClass}>{copy.workbench.ecomProductModeLabel}</legend>
          {/* fieldset+legend 已构成 group（可及名=legend），不再叠加内层 role=group 以免同名双组。 */}
          <div className="grid grid-cols-2 gap-2">
            <SelectableOption selected={productImagesMode === "multi_item"} onSelect={() => setProductImagesMode("multi_item")} className="justify-center">
              {copy.workbench.ecomProductModeMultiItem}
            </SelectableOption>
            <SelectableOption selected={productImagesMode === "multi_angle"} onSelect={() => setProductImagesMode("multi_angle")} className="justify-center">
              {copy.workbench.ecomProductModeMultiAngle}
            </SelectableOption>
          </div>
          <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.ecomProductModeHint}</p>
        </fieldset>
      )}

      {/* 商品图（必填 ≥1）：max = 6 − 模特图数（合计 ≤6 联动，D1）；超限由 picker 明确提示不静默截断 */}
      <ReferenceImagesPicker
        onChange={setProductIds}
        max={IMAGE_BUDGET - modelCount}
        inputId="ecom-model-product"
        label={copy.workbench.ecomModelProductLabel}
        uploadLabel={copy.workbench.ecomModelProductUpload}
        overLimitError={copy.workbench.ecomModelOverLimit}
      />

      {/* 模特图（选填 0–N）：max = 6 − 商品图数（合计 ≤6 联动，D1）；不传即纯文生模特 */}
      <ReferenceImagesPicker
        onChange={setModelIds}
        max={IMAGE_BUDGET - productCount}
        inputId="ecom-model-model"
        label={copy.workbench.ecomModelModelLabel}
        uploadLabel={copy.workbench.ecomModelModelUpload}
        overLimitError={copy.workbench.ecomModelOverLimit}
      />
      <p className="-mt-2 mb-[15px] text-[12px] text-ink-faint">{copy.workbench.ecomModelModelHint}</p>

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

      {/* 风格预设（选填，D3）：与自定义风格互斥 —— 填了自定义 → 预设禁用（disabled 语义 + 提示，不靠颜色单独表意） */}
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
          <>
            <div className="grid grid-cols-2 gap-2">
              {styleList.map((s) => (
                <SelectableOption
                  key={s.id}
                  selected={styleId === s.id}
                  disabled={customFilled}
                  onSelect={() => setStyleId(styleId === s.id ? null : s.id)}
                  className="justify-center"
                >
                  {s.name}
                </SelectableOption>
              ))}
            </div>
            {customFilled && <p className="mt-2 text-[12px] text-ink-faint">{copy.workbench.ecomStylePresetDisabledHint}</p>}
          </>
        )}
      </fieldset>

      {/* 自定义风格（D3）：与预设互斥 —— 选了预设 → 本框禁用（disabled 语义 + 提示） */}
      <div className="mb-[15px]">
        <label htmlFor="ecom-model-custom-style" className={labelClass}>
          {copy.workbench.ecomCustomStyleLabel}
        </label>
        <textarea
          id="ecom-model-custom-style"
          value={customStyle}
          onChange={(e) => setCustomStyle(e.target.value)}
          disabled={styleId !== null}
          rows={2}
          placeholder={copy.workbench.ecomCustomStylePlaceholder}
          className="w-full resize-y rounded-field border border-line-gold bg-glass-fill px-4 py-3 text-sm leading-relaxed text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold disabled:cursor-not-allowed disabled:opacity-60"
        />
        {styleId !== null && <p className="mt-1.5 text-[12px] text-ink-faint">{copy.workbench.ecomCustomStyleDisabledHint}</p>}
      </div>

      {/* 自定义补充（D4：取消旧 200 截断、无计数；前端不设 maxLength，BE ≤20000 反滥用上界超限 422） */}
      <AiTextField
        id="ecom-model-custom"
        label={copy.workbench.ecomCustomLabel}
        value={custom}
        onChange={setCustom}
        rows={2}
        placeholder={copy.workbench.ecomCustomPlaceholder}
      />

      {/* 画面比例（默认 1:1） */}
      <AspectRatioSelect value={aspectRatio} onValueChange={setAspectRatio} />

      <p className="mb-3 text-[12px] text-ink-faint">{copy.workbench.ecomModelCompliance}</p>

      <AiLabelToggle checked={applyLabel} onChange={setApplyLabel} />

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="mt-1 w-full" onClick={() => void onGenerate()} disabled={generateDisabled}>
        {model.isPending ? <RefreshCw size={18} strokeWidth={1.8} className="animate-spin" /> : <Sparkles size={18} strokeWidth={1.8} />}
        {model.isPending ? copy.workbench.ecomGenerating : copy.workbench.ecomGenerate}
      </Button>

      {resultTask && (
        <div className="mt-5 border-t border-line-gold pt-5">
          <span className="mb-3 block text-[12.5px] tracking-[.5px] text-ink-soft">{copy.workbench.ecomResultsLabel}</span>
          {/* 单任务结果：进度 → 完成/失败，礼貌播报（对齐外壳结果区）。 */}
          <div role="status" aria-live="polite" aria-atomic="false">
            <ResultTile task={resultTask} />
          </div>
        </div>
      )}
    </Card>
  );
}
