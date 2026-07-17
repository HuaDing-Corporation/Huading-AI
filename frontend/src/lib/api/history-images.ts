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

/**
 * 整套中的单张（FIX2 对齐真实 BE `ImageHistoryDetailItem`，backend/app/schemas/history.py:32-43）：
 * download_url/width/height 均**必填**（BE `str`/`int` 非空——失败张 BE 已 omit、详情只返成功张，故无 null）；
 * theme/label 为 `str | None`。BE 另有 5 个展示元字段（requested/resolved/actual_aspect_ratio、resolved/actual_size，
 * 均 `str | None`）——本 UI 不消费，故类型/ mock 有意省略（FE 读子集，非红线违规）。
 */
export interface HistoryImageSetItem {
  index: number;
  download_url: string; // 原图 bytes（presigned）——BE 必填非空
  width: number;
  height: number;
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
  /**
   * 分类特有的元信息（BE services/image_history.py:418-447 按 category 分支构造）。
   * 🔴 HISTORY-FULL-PROMPT-UI-0001：**把正在读的两个键声明出来** —— 原先整个是 Record<string, unknown>，
   * 读 meta.prompt 拿到 unknown、键名拼错类型层一声不吭（#185 栽过同款：Partial<VideoDetail> 强转让
   * duration_sec / duration_ms 写错也照样编译过）。索引签名保留（其余分类特有键仍开放），但**读的必须声明**。
   */
  meta?: {
    /** image_gen 分类：BE 直接放 meta["prompt"] = tasks[0].topic（image_history.py:426）—— 与标题同源。 */
    prompt?: string;
    /**
     * **仅 ecom_model**：用户填的补充描述（image_history.py:444 的 else 分支 —— PhotoHistoryCategory
     * 只有四值 ["image_gen","ecom_white","ecom_model","cover"]（:26），故 else 只覆盖 ecom_model）。
     * ⚠️ 不是「详情 / 海报」：ecom_detail 走另一个 builder（:545-559，meta 无此键）；海报不是历史分类。
     */
    extra_prompt?: string;
    /**
     * **仅 photo 四类**（image_gen/ecom_white/ecom_model/cover）：与 `items` **同序**的任务 ID
     * （image_history.py:421 `{"task_ids": [task.id for task in tasks]}` —— 无条件写入，且与 :471-490 的
     * `items` 同出一份 `tasks` 列表）。ecom_detail 走另一个 builder（:550-560），**没有这个键**。
     * 用途见 `historyImageSetMediaKey`。
     */
    task_ids?: string[];
    // 其余分类特有键（详情图 output_mode / 白底图 background / 封面 timestamp_sec…）
    [key: string]: unknown;
  };
}

/**
 * 整套里单张的**跨重取稳定身份** —— 喂给 `useMediaUrlRefresh` 的 mediaKey（CONVERGE-0001 · FIX3 的 P1）。
 *
 * 🔴 `item.index` 在两条 BE 分支下**同名、同类型、意思相反**，这正是这条 P1 的根：
 *
 *  · **ecom_detail**（`_replicate_history_detail`）：`index=output.index`（image_history.py:537）读的是
 *    **持久化列** `ecom_replicate_outputs.index`（models.py:585 `Mapped[int]` NOT NULL），且
 *    `UniqueConstraint("job_id","index")`（models.py:570）→ **一个 job 内唯一**。计划期 enumerate 一次写入
 *    （ecom_replicate.py:191/218，status 还是 "planned"），全仓**无任何写回路径**，BE 自己还拿它当主键用
 *    （ecom_replicate.py:395 `EcomReplicateOutput.index == output_index` + `with_for_update()`）
 *    → **跨重取恒定，index 本身就是身份**。
 *    ⚠️ 但**数组位置**会漂：`_safe_replicate_outputs` 按 `status=="succeeded"` 过滤（:257），
 *    陆续成功时先到的那张位置会被后到、index 更小的挤后 —— 故只能认 `item.index`，不能认下标。
 *
 *  · **其余四类**（`_photo_history_detail`）：BE 只查 `status=="done"` 的任务（image_history.py:84）、
 *    按 `created_at` 排序后**重新 enumerate**（:472 `for index, task in enumerate(tasks)`）。排序键固定
 *    给的是稳定的**相对顺序**，但 index 是**绝对位置**，而这个列表的**成员资格随完成情况变** ——
 *    **批量陆续完成时，同一张图会从 index 0 漂到 1、2**（created_at 更早、完成更晚的兄弟插到它前面）
 *    → **index 不是身份**。这四类必须用 `meta.task_ids[index]`（VideoTask.id 是主键，恒定）。
 *    （image_gen 例外：history_id == VideoTask.id（:100）→ 整套恒 1 张，漂不起来；但它同样有 task_ids。）
 *
 * 取不到 task_id 即落回 `item.index`：对 ecom_detail 这是**正解**（上面那条持久化列）；对 photo 四类
 * 只在 BE 契约破了（:421 是无条件写入）时才可能到达，此时手上没有比 index 更好的身份 —— 退回 index
 * 最坏是封顶被放大到 2×N（**有界**），而若改用「整套共享一个 key」，健康兄弟的 onLoad 会替坏图清账
 * → 坏图**永不封顶**（**无界**，正是 FIX2 修的那个洞）。两害取有界者。
 */
export function historyImageSetMediaKey(set: HistoryImageSet, item: HistoryImageSetItem): string {
  const taskId = set.meta?.task_ids?.[item.index];
  return typeof taskId === "string" && taskId ? `task:${taskId}` : `output:${item.index}`;
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

// FIX1（HISTORY-IMAGE-TAB-UI-0001）：归一 API 的图片删除端点被摘掉——#175 的同步删除媒体经 Codex B 三轮审查
// 出七八条 P1（共享 Asset 误删 / 批次半删 / 跨租户路径穿越 / TOCTOU），用户「三拆」改 GC 方案将来补。
// 故此处不再导出图片删除 adapter（不留死代码）；GC 包上线时原样复活。

/** 单张原始尺寸「宽x高」（width/height 均在才给；缺一即 null，UI 不得冒充）。 */
export function historyImageDimensions(item: HistoryImageSetItem): string | null {
  return item.width && item.height ? `${item.width}x${item.height}` : null;
}
