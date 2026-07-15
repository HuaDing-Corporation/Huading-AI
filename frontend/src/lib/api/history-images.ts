import { apiFetch } from "@/lib/api/client";

// 图片历史·统一模块 adapter（HISTORY-UI-0001）—— 收拢归一契约 + fetch，组件只依赖本模块。
// **mock 先行**：端点/字段以需求冻结「统一历史契约」为准（02-方案设计/需求冻结-图片历史统一模块-20260710.md），
// 真实字段以 HISTORY-BE-0001 回执定稿；BE 合并后仅对齐本文件、组件不动。
// 归一后 4 类都映射到这套（BE 负责各类存储归一，UI 只认这套）：
//   列表 GET /api/v1/history/images?category=&page=&page_size=  → { items:[HistoryItem], total, page, page_size }
//   详情 GET /api/v1/history/images/{category}/{id}             → HistoryImageSet（整套，每张原图 download_url + 原始尺寸）

/** 分类键：图片生成/修改 · 电商白底图 · 电商模特图 · 电商详情图 · 封面（HISTORY-IMAGE-TAB-UI-0001 加 cover）。 */
export type HistoryCategory = "image_gen" | "ecom_white" | "ecom_model" | "ecom_detail" | "cover";
export const HISTORY_CATEGORIES: HistoryCategory[] = ["image_gen", "ecom_white", "ecom_model", "ecom_detail", "cover"];

/** 列表卡片（各类取合适字段归一到这套）。 */
export interface HistoryItem {
  id: string;
  category: HistoryCategory | string;
  title: string; // 主题 / prompt 摘要 / 文件名
  cover_url: string; // 缩略或首图的可下载 URL（presigned）
  created_at: string; // ISO
  status: string; // completed/partial_failed/failed/ready 等（单图类恒 ready）
  item_count: number; // 该条包含图片数（套图=5/12，单图=1）
}

export interface HistoryListResponse {
  items: HistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 整套中的单张：原图可下载 URL + 原始尺寸（缺失即 null，不冒充）。 */
export interface HistoryImageSetItem {
  index: number;
  download_url?: string | null; // 原图 bytes（presigned）；缺失→前端禁用态、不死链
  width?: number | null;
  height?: number | null;
  theme?: string | null; // 分类特有标注（详情图页面主题机器键）
  label?: string | null; // 通用标签（白底图/模特图等友好名）
}

/** 重开整套（详情）。 */
export interface HistoryImageSet {
  id: string;
  category: HistoryCategory | string;
  created_at: string;
  status: string;
  items: HistoryImageSetItem[];
  meta?: Record<string, unknown>; // 分类特有（如详情图 output_mode / 规划信息，可选）
}

const BASE = "/api/v1/history/images";
export const HISTORY_PAGE_SIZE = 20;

function qs(params: Record<string, string | number>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) sp.set(key, String(value));
  return sp.toString();
}

/** 列表（租户作用域，按 created_at 倒序，分页）。category 省略 = 全部图片（BE 归一 API 的 category 为 Optional）。 */
export function listHistoryImages(input: {
  category?: HistoryCategory;
  page?: number;
  page_size?: number;
}): Promise<HistoryListResponse> {
  const page = input.page ?? 1;
  const page_size = input.page_size ?? HISTORY_PAGE_SIZE;
  const params: Record<string, string | number> = { page, page_size };
  if (input.category) params.category = input.category; // 省略 → 全部图片
  return apiFetch<HistoryListResponse>(`${BASE}?${qs(params)}`, { method: "GET" });
}

/** 详情：重开整套（跨租户 404）。 */
export function getHistoryImageSet(category: HistoryCategory | string, id: string): Promise<HistoryImageSet> {
  return apiFetch<HistoryImageSet>(`${BASE}/${encodeURIComponent(category)}/${encodeURIComponent(id)}`, { method: "GET" });
}

/**
 * 删除一条图片历史（硬删，DELETE /history/images/{category}/{history_id}）。
 * ⚠️ mock 先行：BE 端点由 HISTORY-REFACTOR-BE-0001（Codex A）补，硬删语义、文案 deleteConfirmHard；
 * 契约（尤其 path 段名 history_id、返回体）以 BE 回执定稿，本文件届时仅对齐、组件不动。跨租户 404。
 */
export function deleteHistoryImage(category: HistoryCategory | string, id: string): Promise<void> {
  return apiFetch<void>(`${BASE}/${encodeURIComponent(category)}/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** 单张原始尺寸「宽x高」（width/height 均在才给；缺一即 null，UI 不得冒充）。 */
export function historyImageDimensions(item: HistoryImageSetItem): string | null {
  return item.width && item.height ? `${item.width}x${item.height}` : null;
}
