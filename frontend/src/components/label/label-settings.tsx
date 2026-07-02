"use client";

import { useEffect, useState } from "react";
import { Info, ShieldCheck } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useLabelSettings, useUpdateLabelSettings } from "@/lib/api/hooks";
import type { LabelPosition } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MAX_TEXT = 20;

// 2 列布局空间映射位置：上排=上方位、下排=下方位、底部居中占整行。
const POSITIONS: { id: LabelPosition; label: string; span?: boolean }[] = [
  { id: "tl", label: copy.label.posTl },
  { id: "tr", label: copy.label.posTr },
  { id: "bl", label: copy.label.posBl },
  { id: "br", label: copy.label.posBr },
  { id: "bc", label: copy.label.posBc, span: true }
];

// 预览水印按位置绝对定位。
const POS_CLASS: Record<LabelPosition, string> = {
  tl: "top-2 left-2",
  tr: "top-2 right-2",
  bl: "bottom-2 left-2",
  br: "bottom-2 right-2",
  bc: "bottom-2 left-1/2 -translate-x-1/2"
};

/**
 * 深度合成标识「样式设置」页（LABEL-UI-0001 / 语义统一 LABEL-TOGGLE-UI-0001）——位置 + 文案 +
 * 实时预览 + 生效方式说明 + 保存。职责：**配置显式标识的样式/文案**；是否应用由每次生成时的
 * 「AI 生成标识」开关决定（默认关，见 AiLabelToggle）。此页不含应用开关，update 只发 position/text
 * （不含 enabled）。唯一 hooks 调用方。
 */
export function LabelSettings() {
  const { data, isLoading, isError, refetch } = useLabelSettings();
  const update = useUpdateLabelSettings();

  const [position, setPosition] = useState<LabelPosition>("br");
  const [text, setText] = useState("AI 生成");
  const [seeded, setSeeded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // 首次拿到后端设置后回填一次（之后用户编辑为准）。
  useEffect(() => {
    if (data && !seeded) {
      setPosition(data.position);
      setText(data.text.slice(0, MAX_TEXT)); // 防御：后端若违约返超长，回填也不越界
      setSeeded(true);
    }
  }, [data, seeded]);

  const posLabel = POSITIONS.find((p) => p.id === position)?.label ?? "";

  const onSave = async () => {
    setError(null);
    setSaved(false);
    const trimmed = text.trim();
    if (!trimmed) {
      setError(copy.label.textRequired);
      return;
    }
    try {
      // 仅发 position/text；enabled 由后端强制恒真（合规不可关）。
      await update.mutateAsync({ position, text: trimmed });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const saveDisabled = update.isPending || !text.trim();

  return (
    <Card animateIn>
      <CardTitle>{copy.label.settingsTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.label.pageSubtitle}</CardSubtitle>

      {isLoading ? (
        <p className="text-[13px] text-ink-soft">{copy.history.loading}</p>
      ) : isError ? (
        <div className="text-[13px] text-error-fg">
          {copy.history.error}{" "}
          <button type="button" onClick={() => void refetch()} className="text-gold-deep underline">
            {copy.history.retry}
          </button>
        </div>
      ) : (
        <>
          {/* 位置 */}
          <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
            <legend className={labelClass}>{copy.label.positionLabel}</legend>
            <div className="grid grid-cols-2 gap-2">
              {POSITIONS.map((p) => (
                <SelectableOption
                  key={p.id}
                  selected={position === p.id}
                  onSelect={() => setPosition(p.id)}
                  className={p.span ? "col-span-2 justify-center" : "justify-center"}
                >
                  {p.label}
                </SelectableOption>
              ))}
            </div>
          </fieldset>

          {/* 文案 */}
          <div className="mb-[15px]">
            <label htmlFor="label-text" className={labelClass}>
              {copy.label.textLabel}
            </label>
            <Input
              id="label-text"
              value={text}
              maxLength={MAX_TEXT}
              onChange={(e) => setText(e.target.value.slice(0, MAX_TEXT))}
              placeholder={copy.label.textPlaceholder}
            />
            <p className="mt-1 text-[12px] text-ink-faint">{`${text.length}/${MAX_TEXT}`}</p>
          </div>

          {/* 实时预览 */}
          <div className="mb-[15px]">
            <span className={labelClass}>{copy.label.previewLabel}</span>
            <div
              role="img"
              aria-label={`${copy.label.previewLabel}：${posLabel}${text.trim() ? "，" + text.trim() : ""}`}
              className="relative aspect-video w-full overflow-hidden rounded-field border border-line-gold bg-glass-soft"
            >
              <span className="absolute inset-0 flex items-center justify-center text-[12px] text-ink-faint">画面预览示意</span>
              {text.trim() && (
                <span className={`absolute ${POS_CLASS[position]} rounded bg-ink/55 px-2 py-0.5 text-[11px] text-white`}>
                  {text.trim()}
                </span>
              )}
            </div>
          </div>

          {/* 生效方式说明：此页配样式，是否应用由每次生成时的开关决定（此页不含应用开关） */}
          <div className="mb-[15px] flex items-start gap-2.5 rounded-field border border-line-gold bg-glass-fill px-3 py-2.5">
            <ShieldCheck size={16} strokeWidth={1.8} className="mt-0.5 flex-none text-gold-deep" />
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
                <Info size={12} strokeWidth={2} className="flex-none text-ink-soft" /> {copy.label.applyNoticeTitle}
              </p>
              <p className="mt-0.5 text-[12px] text-ink-soft">{copy.label.applyNoticeHint}</p>
            </div>
          </div>

          {error && (
            <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
              {error}
            </p>
          )}

          <Button variant="primary" size="lg" className="w-full" onClick={() => void onSave()} disabled={saveDisabled}>
            {update.isPending ? copy.label.saving : saved ? copy.label.saved : copy.label.save}
          </Button>
          {/* 保存中/成功对屏幕阅读器礼貌通报(对齐 copywriting-form/cover-panel 既有约定)。 */}
          <p className="sr-only" role="status" aria-live="polite">
            {update.isPending ? copy.label.saving : saved ? copy.label.saved : ""}
          </p>
        </>
      )}
    </Card>
  );
}
