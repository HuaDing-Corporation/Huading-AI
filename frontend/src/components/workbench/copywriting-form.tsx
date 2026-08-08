"use client";

import { useState } from "react";
import { Copy, RefreshCw, Sparkles } from "lucide-react";

import { copyToClipboard } from "@/lib/clipboard";
import { errorText } from "@/lib/api/error-text";
import { copyFailureBilling } from "@/lib/api/copy-billing";
import { useGenerateTitles, useGenerateTopics, useRewriteCopy, useSaveCopyDraft } from "@/lib/api/hooks";
import type { CopyMode, CopyPlatform, CopyRewriteRequest, CopyRewriteResult } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "@/components/ui/select";
import { SelectableOption } from "@/components/ui/selectable-option";
import { AiTextField } from "@/components/workbench/ai-text-field";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

const MODE_OPTIONS: { id: CopyMode; label: string }[] = [
  { id: "smart", label: copy.workbench.copyModeSmart },
  { id: "custom", label: copy.workbench.copyModeCustom },
  { id: "auto", label: copy.workbench.copyModeAuto }
];
const COUNT_OPTIONS = [3, 4, 5];
const PLATFORM_OPTIONS: { id: "any" | CopyPlatform; label: string }[] = [
  { id: "any", label: copy.workbench.copyPlatformAny },
  { id: "douyin", label: copy.workbench.copyPlatformDouyin },
  { id: "xiaohongshu", label: copy.workbench.copyPlatformXiaohongshu }
];

type VideoTarget = "avatar_talk" | "seedance_i2v";

/** label + 候选 chips（点击复制）— 标题区与话题区同构，抽此本地组件消除复制粘贴。 */
function CopyChipGroup({ label, items, onCopy }: { label: string; items: string[]; onCopy: (text: string) => void }) {
  if (items.length === 0) return null;
  return (
    <div className="mb-[15px]">
      <label className={labelClass}>{label}</label>
      <div className="flex flex-wrap gap-2">
        {items.map((item, i) => (
          <Chip key={i} onClick={() => onCopy(item)} title={copy.workbench.copyCopy}>
            {item}
          </Chip>
        ))}
      </div>
    </div>
  );
}

/**
 * 文案仿写 + 标题/话题生成 (mode="copywriting") workbench container — 第四模式。
 * 同步 REST（无确认窗）：粘贴参考文案 → 选改写模式 → 一键并发 rewrite+titles+topics →
 * 结果区编辑/复制/保存到历史/一键串联进口播·电商表单。
 * The ONLY hooks caller here；子控件全是 props（AiTextField/SelectableOption/Chip/Select）。
 * 一键串联只种 script（result_text）→ 经 onUseInVideo 回调由 page 层 prefill 目标表单。
 *
 * ── PRICING-UI-0001 §五 ──────────────────────────────────────────────────────────────
 * 🔴 本注释此前写着「**不扣费**」，同时界面上一个字的价格披露都没有。两条都不成立了：
 *    BE PR #239（`services/copy.py` `_generate_billed_copy`）给 rewrite / titles / topics 三个端点
 *    各加了 reserve→settle，单价取 `estimate_copy_quota` 的 `capability="llm" unit="call"` 费率
 *    （函数默认 1 积分/次）。本组件一次「生成文案」并发打三个端点 → **合计 3 积分**。
 * 🔴 「各自独立降级」也从优点变成了缺陷：titles/topics 失败被静默置空，用户看不到失败、
 *    更无从理解为什么只扣了 1 分而不是 3 分。现在失败必须显式列出（`partFailures`）。
 *    注意这**不依赖 BE 返回 per-endpoint 结果**——三个端点是本组件自己 `Promise.allSettled` 并发调的，
 *    每一路的成败在这里当场就知道（详见 onGenerate 的注释）。
 */
export function CopywritingForm({ onUseInVideo }: { onUseInVideo?: (target: VideoTarget, script: string) => void }) {
  const rewrite = useRewriteCopy();
  const titlesGen = useGenerateTitles();
  const topicsGen = useGenerateTopics();
  const saveDraft = useSaveCopyDraft();

  const [sourceText, setSourceText] = useState("");
  const [mode, setMode] = useState<CopyMode>("smart");
  const [instruction, setInstruction] = useState("");
  const [count, setCount] = useState(3);
  const [platform, setPlatform] = useState<"any" | CopyPlatform>("any");

  const [resultText, setResultText] = useState("");
  const [candidates, setCandidates] = useState<CopyRewriteResult[]>([]);
  const [titles, setTitles] = useState<string[]>([]);
  const [topics, setTopics] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** 🔴 §五.3：本次哪几路没出来（各带原因）。空数组 = 全成或还没生成过。 */
  const [partFailures, setPartFailures] = useState<string[]>([]);
  const [copiedFlash, setCopiedFlash] = useState(false);
  const [saved, setSaved] = useState(false);

  const generating = rewrite.isPending || titlesGen.isPending || topicsGen.isPending;
  const generateDisabled =
    !sourceText.trim() || generating || (mode === "custom" && !instruction.trim());

  const copyText = async (text: string) => {
    // 🔴 CLIPBOARD-TRUTH-0001：只在**真的写进剪贴板**时才闪「已复制」。
    // 旧写法 `await navigator.clipboard?.writeText(text)` 的 `?.` 在非安全上下文短路成 undefined、
    // await 不抛 → 照样 setCopiedFlash(true) → 谎报。现在成功态由 copyToClipboard 的返回布尔驱动。
    // 本组件无复制失败的 UI（不同于 publish 的 copyFailed），失败即静默降级（用户可手动选中复制）。
    if (await copyToClipboard(text)) {
      setCopiedFlash(true);
      window.setTimeout(() => setCopiedFlash(false), 1500);
    }
  };

  /**
   * 一键三出：rewrite 为主产出（失败则报错），titles/topics 不阻断主产出 —— 但**失败必须说出来**。
   *
   * 🔴 §五.3 的实现落点就在这里，且**不需要等 BE 在返回体里带 per-endpoint 结果**：
   *    三个端点是本函数自己用 `Promise.allSettled` 并发调的，`ti.status === "rejected"` 就是
   *    「标题这一路失败了」，原因也在 `ti.reason` 里。此前的代码是 `ti.status === "fulfilled" ? … : []`
   *    ——把失败**当成了空结果**，这才是「降级隐藏」的真身。
   * 🔴 「该项未计费」是可核查的事实，不是安慰话：BE `_generate_billed_copy` 的 except 分支调
   *    `quota.release_copy_quota` 把预留原额释放（不落 settled），所以失败的那一路确实不进账单。
   */
  const onGenerate = async () => {
    const source = sourceText.trim();
    if (!source) return;
    if (mode === "custom" && !instruction.trim()) {
      setError(copy.workbench.copyInstructionRequired);
      return;
    }
    setError(null);
    setPartFailures([]);
    setSaved(false);

    const rewriteReq: CopyRewriteRequest = {
      source_text: source,
      mode,
      instruction: mode === "custom" ? instruction.trim() : undefined,
      n: mode === "auto" ? count : undefined,
      target_platform: platform === "any" ? undefined : platform
    };

    const [rw, ti, to] = await Promise.allSettled([
      rewrite.mutateAsync(rewriteReq),
      titlesGen.mutateAsync({ source_text: source }),
      topicsGen.mutateAsync({ source_text: source })
    ]);

    if (rw.status === "fulfilled") {
      const results = rw.value.results ?? [];
      setCandidates(mode === "auto" ? results : []);
      setResultText(results[0]?.text ?? "");
    } else {
      setError(errorText(rw.reason));
    }
    setTitles(ti.status === "fulfilled" ? ti.value.titles ?? [] : []);
    setTopics(to.status === "fulfilled" ? to.value.topics ?? [] : []);
    // 失败的那几路逐条列出（含各自原因——可能一路 502 上游失败、另一路 403 配额不足）。
    // 🔴 FIX6 · P1-2：措辞按 `copyFailureBilling` 分流 —— **rejection 不等于"服务端没扣钱"**。
    //    拿到服务端自己生成的 failed outcome 才说「未计费」；没拿到响应就只说没完成、指向用量记录。
    //    变异：把下面这行换成恒用 `copyPartFailedReleased` → `copywriting-form.billing.test.tsx`
    //          的「网络中断不许承诺未计费」会红。
    setPartFailures(
      [
        [copy.workbench.copyPartTitles, ti] as const,
        [copy.workbench.copyPartTopics, to] as const
      ]
        .filter(([, r]) => r.status === "rejected")
        .map(([part, r]) => {
          const reason = (r as PromiseRejectedResult).reason;
          const say =
            copyFailureBilling(reason) === "released"
              ? copy.workbench.copyPartFailedReleased
              : copy.workbench.copyPartFailedUnknown;
          return say(part, errorText(reason));
        })
    );
  };

  const onSave = async () => {
    const result = resultText.trim();
    if (!result || saveDraft.isPending) return;
    setError(null);
    try {
      await saveDraft.mutateAsync({
        source_text: sourceText.trim(),
        result_text: result,
        titles: titles.length ? titles : undefined,
        topics: topics.length ? topics : undefined,
        mode,
        target_platform: platform === "any" ? undefined : platform
      });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const hasResult = resultText.trim().length > 0 || titles.length > 0 || topics.length > 0;
  const useDisabled = !resultText.trim();

  return (
    <Card animateIn>
      <CardTitle>{copy.workbench.copyTitle}</CardTitle>
      <CardSubtitle className="mb-[18px] mt-1">{copy.workbench.copySubtitle}</CardSubtitle>

      <AiTextField
        id="copy-source"
        label={copy.workbench.copySourceLabel}
        value={sourceText}
        onChange={setSourceText}
        rows={6}
        placeholder={copy.workbench.copySourcePlaceholder}
      />

      <div className="mb-[15px]">
        <label className={labelClass}>{copy.workbench.copyModeLabel}</label>
        <div className="grid grid-cols-3 gap-2">
          {MODE_OPTIONS.map(({ id, label }) => (
            <SelectableOption
              key={id}
              selected={mode === id}
              onSelect={() => setMode(id)}
              className="justify-center"
            >
              {label}
            </SelectableOption>
          ))}
        </div>
      </div>

      {mode === "custom" && (
        <AiTextField
          id="copy-instruction"
          label={copy.workbench.copyInstructionLabel}
          value={instruction}
          onChange={setInstruction}
          rows={2}
          placeholder={copy.workbench.copyInstructionPlaceholder}
        />
      )}

      <div className="mb-[15px] grid grid-cols-2 gap-3">
        {mode === "auto" && (
          <div>
            <label className={labelClass}>{copy.workbench.copyCountLabel}</label>
            <Select value={String(count)} onValueChange={(v) => setCount(Number(v))}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {COUNT_OPTIONS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <div className={mode === "auto" ? undefined : "col-span-2"}>
          <label className={labelClass}>{copy.workbench.copyPlatformLabel}</label>
          <Select value={platform} onValueChange={(v) => setPlatform(v as "any" | CopyPlatform)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PLATFORM_OPTIONS.map(({ id, label }) => (
                <SelectItem key={id} value={id}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <p className="mb-2 text-[12px] text-ink-faint">{copy.workbench.copyCompliance}</p>
      {/* 🔴 §五.1/§五.2：扣费披露 —— 与其它功能（反推计费门 / 视频 estimate）口径一致，在**发起之前**告知。 */}
      <p className="mb-3 text-[12px] leading-relaxed text-ink-soft">{copy.workbench.copyPriceDisclosure}</p>

      {error && (
        <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      {/* 🔴 §五.3：部分失败**必须可见**——不然用户只看到「少了话题」，也不知道为什么只扣了 2 分。 */}
      {partFailures.length > 0 && (
        <ul role="alert" className="mb-3 space-y-1 rounded-field bg-error-bg px-3 py-2 text-[12.5px] text-error-fg">
          {partFailures.map((msg) => (
            <li key={msg}>{msg}</li>
          ))}
        </ul>
      )}

      {!error && !sourceText.trim() && (
        <p className="mb-3 text-[12.5px] text-ink-soft" aria-live="polite">
          {copy.workbench.copySourceRequired}
        </p>
      )}

      <Button
        variant="primary"
        size="lg"
        className="mt-1 w-full"
        onClick={onGenerate}
        disabled={generateDisabled}
      >
        {generating ? (
          <RefreshCw size={18} strokeWidth={1.8} className="animate-spin" />
        ) : (
          <Sparkles size={18} strokeWidth={1.8} />
        )}
        {generating ? copy.workbench.copyGenerating : copy.workbench.copyGenerate}
      </Button>

      {/* 复制成功无障碍公告 */}
      <p className="sr-only" role="status" aria-live="polite">
        {copiedFlash ? copy.workbench.copyCopied : ""}
      </p>

      {hasResult && (
        <div className="mt-5 border-t border-line-gold pt-5">
          {candidates.length > 1 && (
            <div className="mb-[15px]">
              <label className={labelClass}>{copy.workbench.copyCandidatesLabel}</label>
              <div className="flex flex-col gap-2">
                {candidates.map((c, i) => (
                  <SelectableOption
                    key={i}
                    selected={c.text === resultText}
                    onSelect={() => setResultText(c.text)}
                  >
                    <span className="line-clamp-2 leading-relaxed">{c.text}</span>
                  </SelectableOption>
                ))}
              </div>
            </div>
          )}

          <AiTextField
            id="copy-result"
            label={copy.workbench.copyResultLabel}
            value={resultText}
            onChange={setResultText}
            rows={6}
          />

          <CopyChipGroup label={copy.workbench.copyTitlesLabel} items={titles} onCopy={(t) => void copyText(t)} />
          <CopyChipGroup label={copy.workbench.copyTopicsLabel} items={topics} onCopy={(t) => void copyText(t)} />

          <div className="flex flex-wrap gap-2">
            <Button variant="soft" size="sm" onClick={() => void copyText(resultText)} disabled={useDisabled}>
              <Copy size={14} strokeWidth={2} /> {copiedFlash ? copy.workbench.copyCopied : copy.workbench.copyCopy}
            </Button>
            <Button variant="soft" size="sm" onClick={() => void onSave()} disabled={useDisabled || saveDraft.isPending}>
              {saveDraft.isPending ? copy.workbench.copySaving : saved ? copy.workbench.copySaved : copy.workbench.copySave}
            </Button>
            <Button
              variant="soft"
              size="sm"
              onClick={() => onUseInVideo?.("avatar_talk", resultText.trim())}
              disabled={useDisabled}
            >
              {copy.workbench.copyUseInAvatar}
            </Button>
            <Button
              variant="soft"
              size="sm"
              onClick={() => onUseInVideo?.("seedance_i2v", resultText.trim())}
              disabled={useDisabled}
            >
              {copy.workbench.copyUseInEcom}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
