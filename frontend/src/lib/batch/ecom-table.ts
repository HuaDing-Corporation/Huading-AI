import * as XLSX from "xlsx";

import type { EcomTableRow } from "@/lib/api/types";

export const BATCH_MAX_ROWS = 30; // 业务上限（单批 ≤30，对齐后端）
export const BATCH_PARSE_MAX_BYTES = 5 * 1024 * 1024; // 解析防御：文件 ≤5MB
export const BATCH_PARSE_MAX_ROWS = 500; // 解析防御：行数硬帽，畸形/超大表直接拒

/** 解析防御错误码（form 映射到友好文案）。 */
export const PARSE_ERR_TOO_LARGE = "BATCH_PARSE_TOO_LARGE";
export const PARSE_ERR_TOO_MANY_ROWS = "BATCH_PARSE_TOO_MANY_ROWS";

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

/** 解析工作簿二进制 → 商品表草稿行（纯函数，可独立测；不依赖 File API）。仅取首个 sheet；>500 行直接拒。 */
export function parseEcomWorkbook(data: ArrayBuffer | Uint8Array): EcomRowDraft[] {
  const wb = XLSX.read(data, { type: "array" });
  const sheetName = wb.SheetNames[0]; // 仅取首个工作表（防多 sheet 畸形）
  if (!sheetName) return [];
  const json = XLSX.utils.sheet_to_json<Record<string, unknown>>(wb.Sheets[sheetName], { defval: "" });
  if (json.length > BATCH_PARSE_MAX_ROWS) throw new Error(PARSE_ERR_TOO_MANY_ROWS); // 行数硬帽
  return json.map((r) => ({
    product_name: pick(r, COL_ALIASES.product_name),
    selling_points: pick(r, COL_ALIASES.selling_points),
    image_url: pick(r, COL_ALIASES.image_url) || undefined
  }));
}

/** 解析 Excel/CSV 首个工作表 → 商品表草稿行（前端解析，提交结构化 rows JSON）。文件 ≤5MB。 */
export async function parseEcomTable(file: File): Promise<EcomRowDraft[]> {
  if (file.size > BATCH_PARSE_MAX_BYTES) throw new Error(PARSE_ERR_TOO_LARGE); // 文件大小防御
  const buf = await file.arrayBuffer();
  return parseEcomWorkbook(buf);
}

/**
 * 单行必填校验（逐字对齐后端 services/batches.py）：商品名/卖点必填；图 = image_asset_id 与 image_url
 * **恰好二选一**（都给或都不给均非法，对齐后端 XOR + BATCH_ROW_INVALID）。返回错误字段列表。
 */
export function validateEcomRow(row: EcomRowDraft): Array<"product_name" | "selling_points" | "image"> {
  const errs: Array<"product_name" | "selling_points" | "image"> = [];
  if (!row.product_name?.trim()) errs.push("product_name");
  if (!row.selling_points?.trim()) errs.push("selling_points");
  if (Boolean(row.image_url?.trim()) === Boolean(row.image_asset_id)) errs.push("image"); // XOR：恰好一个
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
