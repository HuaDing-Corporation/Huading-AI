"use client";

import { useCallback, useState } from "react";
import { Layers } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useCreateBatch } from "@/lib/api/hooks";
import type { BatchCommon, BatchRequest, PromptSetRow } from "@/lib/api/types";
import { BATCH_MAX_ROWS } from "@/lib/batch/ecom-table";
import { Button } from "@/components/ui/button";
import { ReferenceImagesPicker } from "@/components/workbench/reference-images-picker";
import { CommonParams } from "@/components/batch/common-params";
import { BatchEstimateDialog } from "@/components/batch/batch-estimate-dialog";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 批量·提示词组（入口 B，BATCH-PROD-UI-0001）：多行文本(每行一条,去空行/计数/>30 拦) + 参考图(复用
 * ReferenceImagesPicker) + 公共参数 → prompt_set 批次。提交前 estimate 确认。参考图入 common.reference_image_asset_ids。
 */
export function PromptSetForm({ onCreated }: { onCreated: (batchId: string) => void }) {
  const create = useCreateBatch();
  const [text, setText] = useState("");
  const [refIds, setRefIds] = useState<string[]>([]);
  const [common, setCommon] = useState<BatchCommon>({});
  const [error, setError] = useState<string | null>(null);
  const [confirmReq, setConfirmReq] = useState<BatchRequest | null>(null);

  const onCommonChange = useCallback((c: BatchCommon) => setCommon(c), []);

  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean); // 去空行
  const over = lines.length > BATCH_MAX_ROWS;
  const rows: PromptSetRow[] = lines.map((prompt) => ({ prompt })); // 不裁剪：>30 由生成门控真拦截

  const onGenerate = () => {
    if (rows.length < 1 || over) return; // >30 真拦截，不发请求
    setError(null);
    setConfirmReq({ kind: "prompt_set", rows, common: { ...common, reference_image_asset_ids: refIds } });
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
          {over ? copy.batch.overLimitN(lines.length) : copy.batch.promptCount(rows.length)}
        </p>
      </div>

      <ReferenceImagesPicker onChange={setRefIds} inputId="batch-prompt-refs" />

      <CommonParams kind="prompt_set" onChange={onCommonChange} />

      {error && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="w-full" onClick={onGenerate} disabled={rows.length < 1 || over || create.isPending}>
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
