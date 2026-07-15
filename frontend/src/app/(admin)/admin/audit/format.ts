import { copy } from "@/lib/copy";

/**
 * 审计「变更前 → 变更后」值格式化（ADMIN-AUDIT-DIFF-RENDER-FIX-0001）。
 *
 * 病灶：原 `String(before[k])` 把嵌套对象吐成 `[object Object]`、空数组吐成空白、`null` 吐成字面
 * `"null"`、布尔吐成裸英文。这里用一对纯函数收拢：`flattenAuditDiff` 把嵌套对象逐键展开成点号路径
 * 叶子（`subscription.total`），`formatAuditValue` 把叶子标量/数组格式化成中文可读串——**绝不产出
 * `[object Object]`**。契约字段形状来自 BE `backend/app/services/admin_console.py`（见测试注明的行号）。
 */

/** 单个叶子值 → 展示串。null/undefined/空串/空数组 → 「—」；布尔 → 是/否；数字 → zh-CN 千分位；数组 → 元素逐个格式化后 `, ` 连接。 */
export function formatAuditValue(value: unknown): string {
  if (value === null || value === undefined) return copy.admin.auditEmpty;
  if (typeof value === "boolean") return value ? copy.admin.auditBoolTrue : copy.admin.auditBoolFalse;
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("zh-CN") : copy.admin.auditEmpty;
  if (typeof value === "string") return value === "" ? copy.admin.auditEmpty : value;
  if (Array.isArray(value)) return value.length === 0 ? copy.admin.auditEmpty : value.map(formatAuditValue).join(", ");
  // 兜底：正常走不到（对象在 flatten 阶段已被展开成叶子），但即便走到也绝不吐 [object Object]。
  const entries = Object.entries(value as Record<string, unknown>);
  return entries.length === 0 ? copy.admin.auditEmpty : entries.map(([k, v]) => `${k}: ${formatAuditValue(v)}`).join("；");
}

/** 一行 diff 叶子：`key` 为点号路径；`before`/`after` 为已格式化串（缺席即该侧无此键——只显另一侧、不画箭头）。 */
export interface AuditDiffLeaf {
  key: string;
  before?: string;
  after?: string;
}

/** 递归把嵌套对象展平成「点号路径 → 叶子值」Map（数组/标量/null/空对象为叶子，不再深入）；保持键的插入顺序。 */
function flattenLeaves(obj: Record<string, unknown>): Map<string, unknown> {
  const out = new Map<string, unknown>();
  const walk = (node: Record<string, unknown>, prefix: string) => {
    for (const [k, v] of Object.entries(node)) {
      const path = prefix ? `${prefix}.${k}` : k;
      const isPlainObject = v !== null && typeof v === "object" && !Array.isArray(v);
      if (isPlainObject && Object.keys(v as Record<string, unknown>).length > 0) {
        walk(v as Record<string, unknown>, path);
      } else {
        out.set(path, v);
      }
    }
  };
  walk(obj, "");
  return out;
}

/**
 * before/after 快照 → 有序叶子列表。键序 = union(flatten(before) 键序, 再 after 独有的新键)——沿用原
 * 「先 before 键、再 after 独有键」语义。只在一侧出现的键 → 只带那一侧值（组件据此不画箭头）。
 */
export function flattenAuditDiff(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null
): AuditDiffLeaf[] {
  const b = flattenLeaves(before ?? {});
  const a = flattenLeaves(after ?? {});
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const k of b.keys()) {
    keys.push(k);
    seen.add(k);
  }
  for (const k of a.keys()) if (!seen.has(k)) keys.push(k);
  return keys.map((key) => ({
    key,
    ...(b.has(key) ? { before: formatAuditValue(b.get(key)) } : {}),
    ...(a.has(key) ? { after: formatAuditValue(a.get(key)) } : {})
  }));
}
