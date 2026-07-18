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

/**
 * 一行 diff 叶子：`key` 为点号路径；`before`/`after` 为已格式化串（缺席即该侧无此键——只显另一侧、不画箭头）。
 * `changed`（ADMIN-AUDIT-DIFF-NOISE-0001）：本键是否发生变化——**单边键**（只在 before 或只在 after，如首次分配
 * 槽位）恒为 true；两边都有则**深比较原始叶子值**（#172 FIX1：判定归判定、展示归展示——用格式化后字符串判
 * `changed` 会把 `1→"1"`、`""/[]/{}→null` 都折成「显示一致」而误判「未变」，抹掉真实变更；实现见 deepEqualLeaf）。
 * 组件据此把变化键默认展示、未变键折叠（信息只折叠不删）。
 */
export interface AuditDiffLeaf {
  key: string;
  before?: string;
  after?: string;
  changed: boolean;
}

/**
 * 长值中截（审计追溯要全值可得——组件层另配 sr-only 全值 + title 兜住，不只靠对读屏不可靠的 title）。
 * **只截 UUID 形**（纯 hex 数字 + 连字符、超阈值 24 的长不透明串，如 `subscription.id`/`target_id` 的
 * 36 位 UUID）。错误码（`TASK_RETRY_ENQUEUE_FAILED`，含非 hex 字母/下划线）、中英错误消息（含 CJK/空格/
 * 标点）、`plan_code`/数字等，因不匹配 UUID 形而天然**不截、完整显示**——审计追溯不误伤可读字段。
 */
const UUID_SHAPED = /^[0-9a-fA-F-]+$/;
export function truncateAuditValue(value: string, threshold = 24): string {
  if (value.length <= threshold || !UUID_SHAPED.test(value)) return value;
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
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
 * 原始叶子值深比较（FIX1：判定归判定、展示归展示——不能用格式化后字符串判 `changed`，否则 `""/[]/{}→null`
 * 都成「—」vs「—」、`1→"1"` 都成「1」vs「1」而误判「未变」，把「发生了变更」这一事实抹掉，违反审计铁律）。
 * 叶子只可能是标量／数组／null／空对象（非空对象在 flatten 阶段已展开）：基本类型走**严格相等**（`1 !== "1"`
 * 自然算变化）；数组逐元素深比较（`[] === []` 判未变、`[1,2] vs [1,3]` 判变化）。自写实现，不引新依赖。
 */
function deepEqualLeaf(a: unknown, b: unknown): boolean {
  if (a === b) return true; // 标量严格相等 + 同引用（null===null、0===0、false===false）
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => deepEqualLeaf(x, b[i]));
  }
  const ak = Object.keys(a as Record<string, unknown>);
  const bk = Object.keys(b as Record<string, unknown>);
  return ak.length === bk.length && ak.every((k) => deepEqualLeaf((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
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
  return keys.map((key) => {
    const hasBefore = b.has(key);
    const hasAfter = a.has(key);
    const before = hasBefore ? formatAuditValue(b.get(key)) : undefined;
    const after = hasAfter ? formatAuditValue(a.get(key)) : undefined;
    // 判定基于**原始叶子值深比较**（非格式化字符串）：单边键（一侧键不存在，undefined≠null）恒变化；
    // 两边键都在则深比较原始值——`""/[]/{}→null`、`1→"1"` 皆算变化，`[] vs []`、`0 vs 0` 算未变。
    const changed = !hasBefore || !hasAfter || !deepEqualLeaf(b.get(key), a.get(key));
    return {
      key,
      ...(hasBefore ? { before } : {}),
      ...(hasAfter ? { after } : {}),
      changed
    };
  });
}
