import * as XLSX from "xlsx";

import type { EcomTableRow } from "@/lib/api/types";

export const BATCH_MAX_ROWS = 30;

/** 商品表草稿行（预览 + 校验用）：解析后每行；image_asset_id 为本地图上传后回填。 */
export interface EcomRowDraft {
  product_name: string;
  selling_points: string;
  image_url?: string; // 表内外链
  image_asset_id?: string; // 本地图上传→素材接口换 asset_id
}

// 列名兼容中英文（大小写/空格不敏感）。
const COL_ALIASES: Record<keyof Pick<EcomRowDraft, "product_name" | "selling_points" | "image_url">, string[]> = {
  product_name: ["product_name", "商品名", "商品名称", "name", "标题"],
  selling_points: ["selling_points", "卖点", "卖点/主题", "selling point", "主题"],
  image_url: ["image_url", "商品图", "图片", "图", "image", "url"]
};

function pick(row: Record<string, unknown>, aliases: string[]): string {
  const keys = Object.keys(row);
  for (const alias of aliases) {
    const hit = keys.find((k) => k.trim().toLowerCase() === alias.toLowerCase());
    if (hit != null) return String(row[hit] ?? "").trim();
  }
  return "";
}

/** 解析工作簿二进制 → 商品表草稿行（纯函数，可独立测；不依赖 File API）。 */
export function parseEcomWorkbook(data: ArrayBuffer | Uint8Array): EcomRowDraft[] {
  const wb = XLSX.read(data, { type: "array" });
  const sheetName = wb.SheetNames[0];
  if (!sheetName) return [];
  const json = XLSX.utils.sheet_to_json<Record<string, unknown>>(wb.Sheets[sheetName], { defval: "" });
  return json.map((r) => ({
    product_name: pick(r, COL_ALIASES.product_name),
    selling_points: pick(r, COL_ALIASES.selling_points),
    image_url: pick(r, COL_ALIASES.image_url) || undefined
  }));
}

/** 解析 Excel/CSV 首个工作表 → 商品表草稿行（前端解析，提交结构化 rows JSON）。 */
export async function parseEcomTable(file: File): Promise<EcomRowDraft[]> {
  const buf = await file.arrayBuffer();
  return parseEcomWorkbook(buf);
}

/** 单行必填校验（对齐 seam 行契约）：商品名/卖点/图（URL 或已上传 asset_id）。返回错误字段列表。 */
export function validateEcomRow(row: EcomRowDraft): Array<"product_name" | "selling_points" | "image"> {
  const errs: Array<"product_name" | "selling_points" | "image"> = [];
  if (!row.product_name?.trim()) errs.push("product_name");
  if (!row.selling_points?.trim()) errs.push("selling_points");
  if (!row.image_url?.trim() && !row.image_asset_id) errs.push("image");
  return errs;
}

/** 草稿行 → 提交行（image_asset_id 优先于 image_url）。 */
export function toEcomTableRow(row: EcomRowDraft): EcomTableRow {
  return {
    product_name: row.product_name.trim(),
    selling_points: row.selling_points.trim(),
    ...(row.image_asset_id ? { image_asset_id: row.image_asset_id } : row.image_url ? { image_url: row.image_url.trim() } : {})
  };
}
