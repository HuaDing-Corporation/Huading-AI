"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { joinShotSection, type WorkbenchPrefill } from "@/lib/api/reverse-prompt";
import { copy } from "@/lib/copy";

/** 项形态：长文本 = 就地可编辑 textarea（编辑结果即实际带入值）；短值 = 只读展示 + 勾选。 */
type PrefillItemKind = "text" | "value";

interface PrefillItem {
  /** prefill 上的属性名；`shotSection` 是**伪项**（分镜段最终拼进主提示词，见 buildItems 注释）。 */
  key: string;
  label: string;
  kind: PrefillItemKind;
  /** text 项的初始内容（可编辑） */
  text: string;
  /** value 项的只读展示串 */
  display?: string;
  /**
   * 伪项专用：本项最终**拼进**哪个字段（分镜段 → video_gen 的 `prompt` / seedance_i2v 的 `scenePrompt`）。
   * 🔴 写成字段而不是在 composePrefill 里写死 "prompt"：两个模块的主提示词字段名**不同**，写死会让
   *    seedance 的分镜静默丢失（勾了也没带进去）——正是「点了没用的开关」。
   */
  joinInto?: string;
}

/** 秒数 → 展示串；undefined 原样传下去（= 不产生该项）。 */
function durationDisplay(sec: number | undefined): string | undefined {
  return sec === undefined ? undefined : copy.reverse.applyDurationSec(sec);
}
/** 布尔 → 开启/关闭；undefined = 不产生该项。⚠️ false 要给「关闭」而非 undefined —— 它仍是一条可勾选的项。 */
function onOffDisplay(flag: boolean | undefined): string | undefined {
  if (flag === undefined) return undefined;
  return flag ? copy.reverse.applyValueOn : copy.reverse.applyValueOff;
}

/** 长文本项给 4 行（主提示词 / 画面提示词 / 分镜段），其余 2 行。新增长文本键要一并加进来。 */
const TALL_TEXT_KEYS = new Set(["prompt", "scenePrompt", "shotSection"]);

/**
 * 由载荷推导「本次会带过去的每一项」（REVERSE-DEEP-UI-0001 · 范围3）。
 *
 * 🔴 **只列 prefill 里真实存在的键** —— 目标模块接不住的字段本就不会出现在载荷里（映射层已按模块裁好），
 *    故此处天然满足「接不了的项不显示，不许显示一个点了没用的开关」。
 * 🔴 分镜表（§八 M2 改版）：BE 现在把分镜段作为**独立键** `shot_section`（已拼好、含段头）单独给出，
 *    主提示词里**不再含**这一段。故这里直接列成两项，勾选时把 shot_section 接在主提示词末尾（见 composePrefill）——
 *    **只拼不拆**，上一版靠正则认段头把主提示词切开的做法已随契约删除。
 *    BE 没给 shot_section（图片反推 / 老结构 / photo 模块）→ 不产生分镜项，不造死开关。
 * 顺序：正文内容 → 修饰项（负面/总控）→ 参数项（比例/时长/音频），由重到轻。
 */
function buildItems(prefill: WorkbenchPrefill): PrefillItem[] {
  const items: PrefillItem[] = [];
  const pushText = (key: string, label: string, v: string | undefined) => {
    if (v !== undefined) items.push({ key, label, kind: "text", text: v });
  };
  const pushValue = (key: string, label: string, display: string | undefined) => {
    if (display !== undefined) items.push({ key, label, kind: "value", text: "", display });
  };
  /**
   * 分镜段伪项 —— 紧跟在它所依附的主提示词项之后（`joinInto` 指明拼进谁）。
   * BE 未给 shot_section → 整项不出现。
   */
  const pushShotSection = (joinInto: string, shotSection: string | undefined) => {
    if (!shotSection) return;
    items.push({ key: "shotSection", label: copy.reverse.applyItemShots, kind: "text", text: shotSection, joinInto });
  };

  switch (prefill.target) {
    case "avatar_talk":
      pushText("topic", copy.reverse.applyItemTopic, prefill.topic);
      pushText("script", copy.reverse.applyItemScript, prefill.script);
      break;
    case "seedance_i2v":
      pushText("topic", copy.reverse.applyItemTopic, prefill.topic);
      pushText("scenePrompt", copy.reverse.applyItemScenePrompt, prefill.scenePrompt);
      // §八 M2：seedance 的主提示词是 scene_prompt → 分镜拼进 scenePrompt（不是 prompt，本模块压根没有 prompt 键）
      pushShotSection("scenePrompt", prefill.shotSection);
      pushText("script", copy.reverse.applyItemScript, prefill.script);
      pushText("negativePrompt", copy.reverse.applyItemNegative, prefill.negativePrompt);
      pushValue("durationSec", copy.reverse.applyItemDuration, durationDisplay(prefill.durationSec));
      break;
    case "video_gen":
      pushText("prompt", copy.reverse.applyItemPrompt, prefill.prompt);
      pushShotSection("prompt", prefill.shotSection);
      pushText("negativePrompt", copy.reverse.applyItemNegative, prefill.negativePrompt);
      pushValue("aspectRatio", copy.reverse.applyItemAspect, prefill.aspectRatio);
      pushValue("durationSec", copy.reverse.applyItemDuration, durationDisplay(prefill.durationSec));
      pushValue("generateAudio", copy.reverse.applyItemGenerateAudio, onOffDisplay(prefill.generateAudio));
      break;
    case "photo":
      // photo 没有 shot_section（§八 M2 只给 video_gen / seedance_i2v）→ 不产生分镜项
      pushText("prompt", copy.reverse.applyItemPrompt, prefill.prompt);
      pushText("masterPrompt", copy.reverse.applyItemMasterPrompt, prefill.masterPrompt);
      pushText("negativePrompt", copy.reverse.applyItemNegative, prefill.negativePrompt);
      pushValue("aspectRatio", copy.reverse.applyItemAspect, prefill.aspectRatio);
      break;
    case "ecom_image":
      pushText("custom", copy.reverse.applyItemCustom, prefill.custom);
      pushValue("aspectRatio", copy.reverse.applyItemAspect, prefill.aspectRatio);
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
  // 编辑过就用编辑值，否则用初值。⚠️ 用 `??` 而非 `||`：清空成 "" 是**用户的意思**，必须压过初值
  //（这正是「编辑结果即实际带入值」的边界情形）。
  const textOf = (i: PrefillItem) => edits[i.key] ?? i.text;
  // 🔴 判据用 `joinInto` 而非键名 "shotSection"：**「有宿主」就是伪项的定义**。写键名等于把同一件事
  //    留两个真源；将来 BE 再给一个可单独取消的依附段（如 audio_section），键名版会把它当独立键下发
  //    给目标表单被静默丢弃，而这一版自动走对分支。
  const pseudo = items.find((i) => i.joinInto !== undefined);
  const source = prefill as unknown as Record<string, unknown>;
  // 🔴 与渲染侧同一判据：`checked` 只记录**用户的覆盖**，未记录 = 默认勾选。写成 `!checked[key]` 会把
  //    「用户什么都没动」当成「全部取消」→ 一项都带不进去。
  const on = (key: string) => checked[key] ?? true;

  for (const item of items) {
    if (!on(item.key)) continue;
    // 🔴 伪项：分镜段本身**不是**目标表单的字段（表单没有 shotSection 控件），它只会被拼进主提示词。
    //    漏掉这个 continue 就会把 shotSection 当独立键下发 —— 目标表单收到一个它不认识的键，静默丢弃。
    if (item.joinInto !== undefined) continue;
    if (item.kind === "value") {
      out[item.key] = source[item.key]; // 短值不可编辑 → 原值直取
      continue;
    }
    // 本项是某个分镜段的宿主（video_gen→prompt / seedance_i2v→scenePrompt）→ 勾选分镜则接在末尾。
    if (pseudo?.joinInto === item.key) {
      out[item.key] = on(pseudo.key) ? joinShotSection(textOf(item), textOf(pseudo)) : textOf(item);
      continue;
    }
    out[item.key] = textOf(item);
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
  // 条目由载荷**派生**（useMemo），不进 state：放 state 会让「换了载荷」的重建晚一帧 —— 重开另一个模块时
  // 首帧先画上一个模块的条目再跳变（Code Review P2）。派生后天然同帧正确。
  const items = useMemo(() => (prefill ? buildItems(prefill) : []), [prefill]);
  // 只把「用户的覆盖」放 state；未记录的键默认勾选（读取处 `?? true`），故首帧也无需等 effect 补默认值。
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [edits, setEdits] = useState<Record<string, string>>({});

  // 每次打开（或换了载荷）清掉上一次的取消/编辑，回到「全部勾选、未编辑」的初态。
  useEffect(() => {
    if (!open || !prefill) return;
    setChecked({});
    setEdits({});
  }, [open, prefill]);

  const uid = useId();
  // clamp 提示里的模块名用**去掉「带入 · 」前缀**的裸名，否则读成「…带入 · 视频生成单条上限 15 秒」（Code Review nit）。
  const moduleName = moduleLabel.replace(/^带入\s*·\s*/, "");
  const isOn = (key: string) => checked[key] ?? true;
  // 🔴 依附项随宿主置灰（Code Review P1）：分镜段最终是**拼进主提示词**才带走的，主提示词一旦不带，
  //    分镜表就无处可去。此时若仍让它可勾选，就成了「点了没用的开关」（规范明令禁止）。
  //    判据同 composePrefill：「有 joinInto」= 依附项，宿主字段名由它自己带（video_gen 是 prompt、
  //    seedance_i2v 是 scenePrompt），两处都不写死键名。
  const dependentDisabled = (item: PrefillItem) => item.joinInto !== undefined && !isOn(item.joinInto);

  // 「哪些 target 带时长」只列一次 —— 列两遍的话，将来多一个带时长的 target 只改了一处，clamp 提示会**静默不再渲染**。
  const durational =
    prefill && (prefill.target === "video_gen" || prefill.target === "seedance_i2v") ? prefill : null;
  const durationClamped = durational?.durationClamped === true;
  const clampedSec = durational?.durationSec;

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
                const disabled = dependentDisabled(item);
                const on = isOn(item.key) && !disabled;
                // useId 前缀：两个结果视图可能同时挂载（工作台 + 历史详情，皆常驻）→ 写死 id 会重复。
                const inputId = `${uid}-${item.key}`;
                const valueId = `${inputId}-value`;
                const noteId = `${inputId}-note`;
                const showClamp = item.key === "durationSec" && durationClamped && clampedSec !== undefined;
                // 短值的**值本身**与 clamp 提示都要挂到勾选框上：否则读屏用户被要求确认一个自己听不到的值，
                // 而 D8 那行「已按上限带入」也不会随控件播报（正是最不该漏掉的一句）。
                const describedBy =
                  [item.kind === "value" ? valueId : null, showClamp ? noteId : null].filter(Boolean).join(" ") ||
                  undefined;
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
                          disabled={disabled}
                          aria-describedby={describedBy}
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
                        // 🔴 readOnly 而非 disabled：不带入 ≠ 不可读。disabled 会让内容对读屏/键盘**彻底消失**，
                        //    用户就无从确认「我取消掉的到底是什么」。语义用 aria-disabled 表达（与下方短值只读同一原则）。
                        readOnly={!on}
                        aria-disabled={!on}
                        onChange={(e) => setEdits((s) => ({ ...s, [item.key]: e.target.value }))}
                        rows={TALL_TEXT_KEYS.has(item.key) ? 4 : 2}
                        className={`mt-2 w-full resize-y rounded-field border border-line-gold bg-glass-soft px-3 py-2 text-[12.5px] leading-relaxed text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold ${on ? "" : "cursor-not-allowed opacity-60"}`}
                      />
                    ) : (
                      // 短值只读：用文本而非 disabled input（只读 ≠ 禁用，语义与视觉都要分清）
                      <p id={valueId} className="mt-1.5 pl-[26px] text-[12.5px] text-ink-soft">
                        {item.display}
                      </p>
                    )}

                    {/* D8：时长被 clamp 时**就近**给明确提示（不许静默改数） */}
                    {showClamp && (
                      <p
                        id={noteId}
                        role="note"
                        className="mt-2 flex items-start gap-1.5 rounded-field bg-error-bg px-2.5 py-1.5 text-[12px] leading-relaxed text-error-fg"
                      >
                        <AlertTriangle size={13} strokeWidth={2} className="mt-0.5 shrink-0" />
                        {sourceDurationSec
                          ? copy.reverse.applyClampNote(Math.round(sourceDurationSec), moduleName, clampedSec as number)
                          : copy.reverse.applyClampNoteNoOrigin(moduleName, clampedSec as number)}
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
            // 一项都没勾 → 确认等于什么都不做（载荷只剩 target，目标表单全跳过、缓冲还结不掉）→ 直接禁用，
            // 不给「点了没反应」的按钮（Code Review P2）。
            disabled={!prefill || items.length === 0 || !items.some((i) => isOn(i.key) && !dependentDisabled(i))}
            onClick={() => prefill && onConfirm(composePrefill(prefill, items, checked, edits))}
          >
            {copy.reverse.applyConfirmSubmit}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
