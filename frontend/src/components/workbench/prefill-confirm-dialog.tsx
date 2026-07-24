"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { joinShotSection, splitShotSection, type WorkbenchPrefill } from "@/lib/api/reverse-prompt";
import { copy } from "@/lib/copy";

/** 项形态：长文本 = 就地可编辑 textarea（编辑结果即实际带入值）；短值 = 只读展示 + 勾选。 */
type PrefillItemKind = "text" | "value";

interface PrefillItem {
  /** prefill 上的属性名；`shots` 是**伪项**（分镜段最终合回 prompt，见 buildItems 注释）。 */
  key: string;
  label: string;
  kind: PrefillItemKind;
  /** text 项的初始内容（可编辑） */
  text: string;
  /** value 项的只读展示串 */
  display?: string;
}

/**
 * 由载荷推导「本次会带过去的每一项」（REVERSE-DEEP-UI-0001 · 范围3）。
 *
 * 🔴 **只列 prefill 里真实存在的键** —— 目标模块接不住的字段本就不会出现在载荷里（映射层已按模块裁好），
 *    故此处天然满足「接不了的项不显示，不许显示一个点了没用的开关」。
 * 🔴 分镜表：§4.3 规定 BE 把 `shot_summary` 作为 `Shots:` 段**追加在主提示词末尾**。要让它能被单独取消，
 *    这里把主提示词拆成「正文 + 分镜段」两项展示；确认时按勾选情况重新合成（见 composePrefill）。
 *    拆不出分镜段（图片反推 / 老结构）→ 不产生分镜项，不造死开关。
 * 顺序：正文内容 → 修饰项（负面/总控）→ 参数项（比例/时长/音频），由重到轻。
 */
function buildItems(prefill: WorkbenchPrefill): PrefillItem[] {
  const items: PrefillItem[] = [];
  const text = (key: string, label: string, v: string | undefined) => {
    if (v !== undefined) items.push({ key, label, kind: "text", text: v });
  };
  const value = (key: string, label: string, display: string | undefined) => {
    if (display !== undefined) items.push({ key, label, kind: "value", text: "", display });
  };
  /** 主提示词 + 可选分镜段（两个 target 共用）。 */
  const promptWithShots = (prompt: string | undefined) => {
    if (prompt === undefined) return;
    const { body, shots } = splitShotSection(prompt);
    items.push({ key: "prompt", label: copy.reverse.applyItemPrompt, kind: "text", text: body });
    if (shots) items.push({ key: "shots", label: copy.reverse.applyItemShots, kind: "text", text: shots });
  };
  const durationDisplay = (sec: number | undefined) =>
    sec === undefined ? undefined : copy.reverse.applyDurationSec(sec);

  switch (prefill.target) {
    case "avatar_talk":
      text("topic", copy.reverse.applyItemTopic, prefill.topic);
      text("script", copy.reverse.applyItemScript, prefill.script);
      break;
    case "seedance_i2v":
      text("topic", copy.reverse.applyItemTopic, prefill.topic);
      text("scenePrompt", copy.reverse.applyItemScenePrompt, prefill.scenePrompt);
      text("script", copy.reverse.applyItemScript, prefill.script);
      text("negativePrompt", copy.reverse.applyItemNegative, prefill.negativePrompt);
      value("durationSec", copy.reverse.applyItemDuration, durationDisplay(prefill.durationSec));
      break;
    case "video_gen":
      promptWithShots(prefill.prompt);
      text("negativePrompt", copy.reverse.applyItemNegative, prefill.negativePrompt);
      value("aspectRatio", copy.reverse.applyItemAspect, prefill.aspectRatio);
      value("durationSec", copy.reverse.applyItemDuration, durationDisplay(prefill.durationSec));
      value(
        "generateAudio",
        copy.reverse.applyItemGenerateAudio,
        prefill.generateAudio === undefined
          ? undefined
          : prefill.generateAudio
            ? copy.reverse.applyValueOn
            : copy.reverse.applyValueOff
      );
      break;
    case "photo":
      promptWithShots(prefill.prompt);
      text("masterPrompt", copy.reverse.applyItemMasterPrompt, prefill.masterPrompt);
      text("negativePrompt", copy.reverse.applyItemNegative, prefill.negativePrompt);
      value("aspectRatio", copy.reverse.applyItemAspect, prefill.aspectRatio);
      break;
    case "ecom_image":
      text("custom", copy.reverse.applyItemCustom, prefill.custom);
      value("aspectRatio", copy.reverse.applyItemAspect, prefill.aspectRatio);
      break;
  }
  return items;
}

/**
 * 按勾选 + 编辑结果重建载荷（REVERSE-DEEP-UI-0001 · 承重门 2 的实现处）。
 * 🔴 未勾选的项 **直接不出现在返回对象里**（不是给空串）—— 目标表单的 `if (x !== undefined)` 会跳过它，
 *    该控件保持用户原值不动。给空串就会把用户已填的内容洗掉，那正是要防的事。
 * 🔴 `durationClamped` 是展示用元数据，不下发给表单。
 */
function composePrefill(
  prefill: WorkbenchPrefill,
  items: PrefillItem[],
  checked: Record<string, boolean>,
  edits: Record<string, string>
): WorkbenchPrefill {
  const out: Record<string, unknown> = { target: prefill.target };
  if (prefill.target === "ecom_image") out.tool = prefill.tool;
  const textOf = (key: string) => edits[key] ?? items.find((i) => i.key === key)?.text ?? "";
  const source = prefill as unknown as Record<string, unknown>;

  for (const item of items) {
    if (!checked[item.key]) continue;
    if (item.key === "shots") continue; // 伪项：由下面的 prompt 分支合回，自身不是独立字段
    if (item.kind === "value") {
      out[item.key] = source[item.key]; // 短值不可编辑 → 原值直取
      continue;
    }
    if (item.key === "prompt") {
      const hasShots = items.some((i) => i.key === "shots");
      // 分镜段勾选 → 原样拼回（与 BE 给的串等价）；取消 → 正文里那段一并不带走。
      out.prompt = hasShots && checked.shots ? joinShotSection(textOf("prompt"), textOf("shots")) : textOf("prompt");
      continue;
    }
    out[item.key] = textOf(item.key);
  }
  return out as unknown as WorkbenchPrefill;
}

export interface PrefillConfirmDialogProps {
  open: boolean;
  /** 待确认的载荷（fillTargetToPrefill 的产物）；null = 不打开 */
  prefill: WorkbenchPrefill | null;
  /** 目标模块名（弹窗标题 + clamp 提示里的模块名），如「带入 · 视频生成」 */
  moduleLabel: string;
  /** 原素材时长（source_media.duration_sec ?? video_analysis.duration_sec），用于 D8 clamp 提示的「原视频 N 秒」 */
  sourceDurationSec?: number | null;
  onConfirm: (prefill: WorkbenchPrefill) => void;
  onCancel: () => void;
}

/**
 * 「带入前确认」弹窗（REVERSE-DEEP-UI-0001 · D3-④）——列出本次会带过去的每一项，**默认全部勾选**（傻瓜式），
 * 主/负面提示词等长文本可就地编辑，短值只读；取消勾选 = 该控件保持原样（不是清空）。
 *
 * 放在 ReversePromptResultView 内部：结果视图是**两个带入入口共用的同一个组件**（工作台反推页 + 历史详情弹窗），
 * 故两入口的带入行为是**结构性一致**的（同一份代码），不是靠两处各写一遍再靠测试对齐。
 *
 * a11y：Radix Dialog 负责焦点陷阱 / Esc / 标题描述关联；每项 label+htmlFor 关联原生 checkbox 与 textarea；
 * 未勾选行**同时**降透明度与显示「不带入（保持原样）」文字（不靠颜色单独表意）；clamp 提示就近放在时长项下方。
 */
export function PrefillConfirmDialog({
  open,
  prefill,
  moduleLabel,
  sourceDurationSec,
  onConfirm,
  onCancel
}: PrefillConfirmDialogProps) {
  const [items, setItems] = useState<PrefillItem[]>([]);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [edits, setEdits] = useState<Record<string, string>>({});

  // 每次打开（或换了载荷）重建条目并回到「全部勾选、未编辑」的初态——不残留上一次的取消/编辑。
  useEffect(() => {
    if (!open || !prefill) return;
    const next = buildItems(prefill);
    setItems(next);
    setChecked(Object.fromEntries(next.map((i) => [i.key, true])));
    setEdits({});
  }, [open, prefill]);

  const durationClamped =
    prefill && (prefill.target === "video_gen" || prefill.target === "seedance_i2v")
      ? prefill.durationClamped === true
      : false;
  const clampedSec =
    prefill && (prefill.target === "video_gen" || prefill.target === "seedance_i2v") ? prefill.durationSec : undefined;

  return (
    <Dialog
      open={open && prefill !== null}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent className="w-[min(94vw,680px)] max-h-[85vh] overflow-y-auto">
        <DialogTitle className="text-base font-semibold text-ink">{moduleLabel}</DialogTitle>
        <DialogDescription className="mt-1.5 text-[12.5px] leading-relaxed text-ink-soft">
          {copy.reverse.applyConfirmDesc}
        </DialogDescription>

        {items.length === 0 ? (
          <p className="py-8 text-center text-[13px] text-ink-soft">{copy.reverse.applyItemsEmpty}</p>
        ) : (
          <fieldset className="mt-3 m-0 min-w-0 border-0 p-0">
            <legend className="sr-only">{moduleLabel}</legend>
            <ul className="flex list-none flex-col gap-2.5 p-0">
              {items.map((item) => {
                const on = checked[item.key] ?? true;
                const inputId = `prefill-item-${item.key}`;
                return (
                  <li
                    key={item.key}
                    className={`rounded-field border border-line-gold bg-glass-fill p-3 transition-opacity ${on ? "" : "opacity-60"}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      {/* 整行 label 可点（触达面积 ≥44px），原生 checkbox 自带键盘/读屏语义 */}
                      <label htmlFor={inputId} className="flex min-h-[24px] cursor-pointer items-center gap-2.5 text-[13px] text-ink">
                        <input
                          id={inputId}
                          type="checkbox"
                          checked={on}
                          onChange={(e) => setChecked((c) => ({ ...c, [item.key]: e.target.checked }))}
                          className="h-4 w-4 flex-none accent-gold-deep"
                        />
                        {item.label}
                      </label>
                      {/* 取消勾选：文字 + 透明度双重表意（不靠颜色单独传达） */}
                      {!on && <span className="shrink-0 text-[12px] text-ink-faint">{copy.reverse.applyItemSkipped}</span>}
                    </div>

                    {item.kind === "text" ? (
                      <textarea
                        id={`${inputId}-text`}
                        // 与同行勾选框区分可及名：否则读屏会读到两个同名控件（勾选框叫「主提示词」，这里叫「主提示词（可编辑内容）」）
                        aria-label={copy.reverse.applyItemEditAria(item.label)}
                        value={edits[item.key] ?? item.text}
                        disabled={!on}
                        onChange={(e) => setEdits((s) => ({ ...s, [item.key]: e.target.value }))}
                        rows={item.key === "prompt" || item.key === "shots" ? 4 : 2}
                        className="mt-2 w-full resize-y rounded-field border border-line-gold bg-glass-soft px-3 py-2 text-[12.5px] leading-relaxed text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold disabled:cursor-not-allowed disabled:opacity-60"
                      />
                    ) : (
                      // 短值只读：用文本而非 disabled input（只读 ≠ 禁用，语义与视觉都要分清）
                      <p className="mt-1.5 pl-[26px] text-[12.5px] text-ink-soft">{item.display}</p>
                    )}

                    {/* D8：时长被 clamp 时**就近**给明确提示（不许静默改数） */}
                    {item.key === "durationSec" && durationClamped && clampedSec !== undefined && (
                      <p
                        role="note"
                        className="mt-2 flex items-start gap-1.5 rounded-field bg-error-bg px-2.5 py-1.5 text-[12px] leading-relaxed text-error-fg"
                      >
                        <AlertTriangle size={13} strokeWidth={2} className="mt-0.5 shrink-0" />
                        {sourceDurationSec
                          ? copy.reverse.applyClampNote(Math.round(sourceDurationSec), moduleLabel, clampedSec)
                          : copy.reverse.applyClampNoteNoOrigin(moduleLabel, clampedSec)}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          </fieldset>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="soft" size="sm" onClick={onCancel}>
            {copy.reverse.applyConfirmCancel}
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={!prefill || items.length === 0}
            onClick={() => prefill && onConfirm(composePrefill(prefill, items, checked, edits))}
          >
            {copy.reverse.applyConfirmSubmit}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
