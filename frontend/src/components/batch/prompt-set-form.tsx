"use client";

import { useCallback, useId, useState } from "react";
import { Layers } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useCreateBatch } from "@/lib/api/hooks";
import type { BatchCommon, BatchRequest, PromptSetRow } from "@/lib/api/types";
import { BATCH_MAX_ROWS } from "@/lib/batch/ecom-table";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ReferenceImagesPicker, type ReferenceImageItem } from "@/components/workbench/reference-images-picker";
import { CommonParams } from "@/components/batch/common-params";
import { BatchEstimateDialog } from "@/components/batch/batch-estimate-dialog";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 批量·提示词组（入口 B，BATCH-PROD-UI-0001 / 配对 BATCH-PROD-UI-0002）：多行文本(每行一条,去空行/计数/
 * >30 拦) + 参考图(复用 ReferenceImagesPicker) + 公共参数 → prompt_set 批次。
 * 「逐行配对参考图」开关（默认关）：
 *  - 关＝共享：参考图整体应用于本批每条 → common.reference_image_asset_ids。
 *  - 开＝配对：第 N 行↔第 N 图，要求参考图数==行数，提交体每行 {prompt, image_asset_id}；数量不等禁用生成。
 */
export function PromptSetForm({ onCreated }: { onCreated: (batchId: string) => void }) {
  const create = useCreateBatch();
  const [text, setText] = useState("");
  const [refItems, setRefItems] = useState<ReferenceImageItem[]>([]);
  const [pairEnabled, setPairEnabled] = useState(false);
  const [common, setCommon] = useState<BatchCommon>({});
  const [error, setError] = useState<string | null>(null);
  const [confirmReq, setConfirmReq] = useState<BatchRequest | null>(null);
  const pairHintId = useId();

  const onCommonChange = useCallback((c: BatchCommon) => setCommon(c), []);

  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean); // 去空行
  const over = lines.length > BATCH_MAX_ROWS;
  const refIds = refItems.map((it) => it.assetId);
  // 配对模式：参考图数须等于提示词行数（不等=禁用生成 + 提示，绝不静默按序截断）。
  const pairMismatch = pairEnabled && refItems.length !== lines.length;
  const blocked = lines.length < 1 || over || pairMismatch;

  const onGenerate = () => {
    if (blocked) return; // >30 / 配对数量不等 真拦截，不发请求
    setError(null);
    const rows: PromptSetRow[] = pairEnabled
      ? lines.map((prompt, i) => ({ prompt, image_asset_id: refItems[i].assetId })) // 按序配对：第 N 行↔第 N 图
      : lines.map((prompt) => ({ prompt }));
    const commonBody: BatchCommon = pairEnabled
      ? { ...common } // 配对：图入行，common 不带 reference_image_asset_ids（避免与逐行图重复应用）
      : { ...common, reference_image_asset_ids: refIds }; // 共享：整体应用于每条
    setConfirmReq({ kind: "prompt_set", rows, common: commonBody });
  };

  const onConfirm = async () => {
    if (!confirmReq) return;
    try {
      const res = await create.mutateAsync(confirmReq);
      setConfirmReq(null);
      onCreated(res.batch_id);
    } catch (err) {
      setError(errorText(err));
      setConfirmReq(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <label htmlFor="batch-prompts" className={labelClass}>
          {copy.batch.promptLabel}
        </label>
        <textarea
          id="batch-prompts"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={8}
          placeholder={copy.batch.promptPlaceholder}
          className="w-full resize-y rounded-field border border-line-gold bg-glass-fill px-4 py-3 text-sm text-ink outline-none transition-shadow placeholder:text-ink-faint focus:border-line-sel focus:shadow-focus-gold"
        />
        <p className={`mt-1 text-[12px] ${over ? "text-error-fg" : "text-ink-faint"}`} role={over ? "alert" : undefined} aria-live="polite">
          {over ? copy.batch.overLimitN(lines.length) : copy.batch.promptCount(lines.length)}
        </p>
      </div>

      <ReferenceImagesPicker onItemsChange={setRefItems} inputId="batch-prompt-refs" />

      {/* 逐行配对开关（默认关）。 */}
      <div className="mb-[15px]">
        <div className="flex items-center justify-between gap-3">
          <span className="text-[12.5px] tracking-[.5px] text-ink-soft">{copy.batch.pairToggleLabel}</span>
          <Switch checked={pairEnabled} onCheckedChange={setPairEnabled} ariaLabel={copy.batch.pairToggleLabel} ariaDescribedby={pairHintId} />
        </div>
        <p id={pairHintId} className="mt-1.5 text-[12px] leading-relaxed text-ink-faint">
          {copy.batch.pairToggleHint}
        </p>

        {!pairEnabled ? (
          // 共享模式：补一句消除歧义。
          <p className="mt-1.5 text-[12px] text-ink-faint">{copy.batch.pairSharedNote}</p>
        ) : (
          <div className="mt-2.5">
            {pairMismatch && (
              <p role="alert" className="mb-2 text-[12.5px] text-error-fg">
                {copy.batch.pairCountMismatch(lines.length, refItems.length)}
              </p>
            )}
            {lines.length > 0 && !over && (
              <>
                <span className={labelClass}>{copy.batch.pairPreviewTitle}</span>
                <ul className="flex flex-col gap-2">
                  {lines.map((prompt, i) => {
                    const item = refItems[i];
                    return (
                      <li key={i} className="flex items-center gap-3 rounded-field border border-line-gold bg-glass-fill px-3 py-2">
                        <span className="w-12 flex-none text-[12px] text-ink-faint">{copy.batch.pairRowLabel(i + 1)}</span>
                        {item ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={item.preview} alt={copy.batch.pairRowLabel(i + 1)} className="h-11 w-11 flex-none rounded-mark border border-line-gold object-cover" />
                        ) : (
                          <span className="flex h-11 w-11 flex-none items-center justify-center rounded-mark border border-dashed border-line-gold text-[11px] text-error-fg">
                            {copy.batch.pairMissingImage}
                          </span>
                        )}
                        <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{prompt}</span>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
          </div>
        )}
      </div>

      <CommonParams kind="prompt_set" onChange={onCommonChange} />

      {error && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="w-full" onClick={onGenerate} disabled={blocked || create.isPending}>
        <Layers size={18} strokeWidth={1.8} /> {copy.workbench.generate}
      </Button>

      <BatchEstimateDialog
        open={!!confirmReq}
        request={confirmReq}
        submitting={create.isPending}
        onConfirm={() => void onConfirm()}
        onCancel={() => setConfirmReq(null)}
      />
    </div>
  );
}
