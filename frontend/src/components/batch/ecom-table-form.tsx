"use client";

import { useCallback, useRef, useState } from "react";
import { Check, ImagePlus, Layers, Upload, X } from "lucide-react";

import { errorText } from "@/lib/api/error-text";
import { useCreateBatch, useUploadImage } from "@/lib/api/hooks";
import type { BatchCommon, BatchRequest } from "@/lib/api/types";
import { BATCH_MAX_ROWS, PARSE_ERR_TOO_LARGE, PARSE_ERR_TOO_MANY_ROWS, parseEcomTable, toEcomTableRow, validateEcomRow, type EcomRowDraft } from "@/lib/batch/ecom-table";
import { isValidDuration } from "@/components/workbench/duration-picker";
import { ALLOWED_UPLOAD_TYPES, MAX_UPLOAD_BYTES } from "@/lib/api/uploads";
import { Button } from "@/components/ui/button";
import { CommonParams } from "@/components/batch/common-params";
import { BatchEstimateDialog } from "@/components/batch/batch-estimate-dialog";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const cellClass = "px-2.5 py-2 text-[12.5px] align-top";

/**
 * 批量·商品表（入口 A，BATCH-PROD-UI-0001）：上传 Excel/CSV(SheetJS 前端解析) → 预览表(行校验：必填
 * 商品名/卖点/图，错误行内联标红) → 每行图(表内 URL 透传 / 本地图上传换 asset_id) → 公共参数 → ecom_table 批次。
 * >30 行拦截提示；提交前 estimate 确认。移动端表格横向滚动。
 */
export function EcomTableForm({ onCreated }: { onCreated: (batchId: string) => void }) {
  const create = useCreateBatch();
  const uploadImg = useUploadImage();
  const [rows, setRows] = useState<EcomRowDraft[]>([]);
  const [common, setCommon] = useState<BatchCommon>({});
  const [parseError, setParseError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadingRow, setUploadingRow] = useState<number | null>(null);
  const [confirmReq, setConfirmReq] = useState<BatchRequest | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const rowFileRef = useRef<HTMLInputElement>(null);
  const pendingRow = useRef<number | null>(null);

  const onCommonChange = useCallback((c: BatchCommon) => setCommon(c), []);

  const onFile = async (file: File | null) => {
    if (!file) return;
    setParseError(null);
    setError(null);
    try {
      const parsed = await parseEcomTable(file); // 不裁剪：全量保留，>30 由生成门控真拦截
      setRows(parsed);
    } catch (err) {
      const msg = (err as Error)?.message;
      setParseError(msg === PARSE_ERR_TOO_LARGE ? copy.batch.ecomTooLarge : msg === PARSE_ERR_TOO_MANY_ROWS ? copy.batch.ecomTooManyRows : copy.batch.ecomParseError);
      setRows([]);
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const onRowImage = async (file: File | null) => {
    const i = pendingRow.current;
    pendingRow.current = null;
    if (rowFileRef.current) rowFileRef.current.value = "";
    if (file == null || i == null) return;
    if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) return setError(copy.errors.uploadType);
    if (file.size > MAX_UPLOAD_BYTES) return setError(copy.errors.uploadTooLarge);
    setError(null);
    setUploadingRow(i);
    try {
      const r = await uploadImg.mutateAsync(file);
      setRows((prev) => prev.map((row, idx) => (idx === i ? { ...row, image_asset_id: r.asset_id } : row)));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setUploadingRow(null);
    }
  };

  const triggerRowUpload = (i: number) => {
    pendingRow.current = i;
    rowFileRef.current?.click();
  };

  // 行必填 + 时长合法 + ≤30（>30 真拦截：禁用 + 显式提示，不裁剪；对齐单条 ecom-video-form 的门控）。
  const durationOk = isValidDuration(common.duration_sec ?? NaN);
  const over = rows.length > BATCH_MAX_ROWS;
  const allValid = rows.length > 0 && rows.every((r) => validateEcomRow(r).length === 0);

  const onGenerate = () => {
    if (!allValid || !durationOk || over) return;
    setError(null);
    setConfirmReq({ kind: "ecom_table", rows: rows.map(toEcomTableRow), common });
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
        <span className={labelClass}>{copy.batch.ecomUploadLabel}</span>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls,.csv"
          className="hidden"
          onChange={(e) => void onFile(e.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="flex w-full items-center justify-center gap-2 rounded-field border border-dashed border-line-gold bg-glass-fill py-5 text-[13px] text-ink-soft transition-colors hover:bg-glass-hover"
        >
          <Upload size={18} strokeWidth={1.8} /> {copy.batch.ecomUploadBtn}
        </button>
        <p className="mt-1.5 text-[12px] text-ink-faint">{copy.batch.ecomUploadHint}</p>
        {parseError && (
          <p role="alert" className="mt-2 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
            {parseError}
          </p>
        )}
        {over && <p role="alert" className="mt-2 text-[12.5px] text-error-fg">{copy.batch.overLimitN(rows.length)}</p>}
      </div>

      {/* 行内图片上传（隐藏 input，按行触发） */}
      <input ref={rowFileRef} type="file" accept={ALLOWED_UPLOAD_TYPES.join(",")} className="hidden" onChange={(e) => void onRowImage(e.target.files?.[0] ?? null)} />

      {rows.length === 0 ? (
        <p className="rounded-field border border-line-gold bg-glass-fill px-3 py-6 text-center text-[13px] text-ink-soft">{copy.batch.ecomEmpty}</p>
      ) : (
        <div className="min-w-0 overflow-x-auto rounded-field border border-line-gold">
          <table className="w-full min-w-[520px] border-collapse text-left">
            <thead>
              <tr className="border-b border-line-gold bg-glass-soft text-[11.5px] text-ink-soft">
                <th className={cellClass + " w-8"}>#</th>
                <th className={cellClass}>{copy.batch.ecomColProduct}</th>
                <th className={cellClass}>{copy.batch.ecomColSelling}</th>
                <th className={cellClass}>{copy.batch.ecomColImage}</th>
                <th className={cellClass + " w-16"}>{copy.batch.ecomColStatus}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const errs = validateEcomRow(row);
                const bad = (f: string) => errs.includes(f as never);
                return (
                  <tr key={i} className="border-b border-line-gold last:border-none">
                    <td className={cellClass + " text-ink-faint"}>{i + 1}</td>
                    <td className={cellClass + (bad("product_name") ? " bg-error-bg text-error-fg" : " text-ink")}>{row.product_name || "—"}</td>
                    <td className={cellClass + (bad("selling_points") ? " bg-error-bg text-error-fg" : " text-ink-soft")}>{row.selling_points || "—"}</td>
                    <td className={cellClass + (bad("image") ? " bg-error-bg" : "")}>
                      {row.image_url ? (
                        <span className="text-ink-soft">URL</span>
                      ) : row.image_asset_id ? (
                        <span className="inline-flex items-center gap-1 text-gold-deep"><Check size={12} strokeWidth={2} /> {copy.batch.ecomRowImageUpload}</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => triggerRowUpload(i)}
                          disabled={uploadingRow === i}
                          className="inline-flex items-center gap-1 rounded-mark border border-line-gold bg-glass-fill px-2 py-1 text-[11.5px] text-gold-deep hover:bg-glass-hover disabled:opacity-50"
                        >
                          <ImagePlus size={12} strokeWidth={2} /> {uploadingRow === i ? copy.workbench.ecomGenerating : copy.batch.ecomRowImageUpload}
                        </button>
                      )}
                    </td>
                    <td className={cellClass}>
                      {errs.length === 0 ? (
                        <span className="inline-flex items-center gap-1 text-[11.5px] text-gold-deep"><Check size={12} strokeWidth={2} /> {copy.batch.ecomRowOk}</span>
                      ) : (
                        // 视觉标红即可；不逐行 role=alert（多无效行会触发大量 assertive 播报），改由下方汇总一次性提示。
                        <span className="inline-flex items-center gap-1 text-[11.5px] text-error-fg"><X size={12} strokeWidth={2} /> {copy.batch.ecomRowError}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {rows.length > 0 && !allValid && (
        <p role="alert" className="text-[12.5px] text-error-fg">{copy.batch.ecomHasInvalid}</p>
      )}

      <CommonParams kind="ecom_table" onChange={onCommonChange} />

      {error && (
        <p role="alert" className="rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
          {error}
        </p>
      )}

      <Button variant="primary" size="lg" className="w-full" onClick={onGenerate} disabled={!allValid || !durationOk || over || create.isPending}>
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
