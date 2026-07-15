import { describe, expect, it } from "vitest";

import { flattenAuditDiff, formatAuditValue } from "./format";

// 审计 diff 格式化承重（ADMIN-AUDIT-DIFF-RENDER-FIX-0001 §四.3）：反假绿最高标准——
// 期望值**手写常量**（不 import 被测函数来算，否则改一处两边同步变、变异永远不红）；
// fixture 形状**照抄 BE**（backend/app/services/admin_console.py，注明行号），不自己发明。
// 变异硬门：把 formatAuditValue 换回 String(v) → 下面「五类各一条」必须全红（见回执实测）。

describe("formatAuditValue · 五类叶子值（逐条确定值 toBe）", () => {
  it("① 数字 → zh-CN 千分位（配合 tabular-nums）；0 → 0", () => {
    expect(formatAuditValue(10000000)).toBe("10,000,000");
    expect(formatAuditValue(20000)).toBe("20,000");
    expect(formatAuditValue(0)).toBe("0");
  });

  it("② 布尔 → 是/否（不再吐裸英文 true/false）", () => {
    expect(formatAuditValue(true)).toBe("是");
    expect(formatAuditValue(false)).toBe("否");
  });

  it("③ null / undefined / 空串 → 破折号（不再吐字面 null / 空白）", () => {
    expect(formatAuditValue(null)).toBe("—");
    expect(formatAuditValue(undefined)).toBe("—");
    expect(formatAuditValue("")).toBe("—");
  });

  it("④ 空数组 → 破折号（不再吐空白）；非空数组 → 元素逐个格式化后 `, ` 连接", () => {
    expect(formatAuditValue([])).toBe("—"); // voice_slot_assign before.speaker_ids（admin_console.py:371）
    expect(formatAuditValue(["S_acme_001"])).toBe("S_acme_001");
    expect(formatAuditValue([1, 2])).toBe("1, 2"); // task_retry 电商复刻 failed_output_indexes（:649-664）
  });

  it("⑤ 字符串原样；数组元素混合类型也按本表格式化", () => {
    expect(formatAuditValue("free")).toBe("free");
    expect(formatAuditValue([1000, 2000])).toBe("1,000, 2,000"); // 数字元素也走千分位
  });

  it("兜底：即便直接喂对象也绝不吐 [object Object]（正常由 flatten 展开，不该走到）", () => {
    expect(formatAuditValue({ total: 10000000 })).not.toContain("[object Object]");
    expect(formatAuditValue({})).toBe("—");
  });
});

describe("flattenAuditDiff · 嵌套展开 + 键序 + 单侧语义", () => {
  it("嵌套对象雷（plan_change，:289-296）→ subscription.* 逐键点号路径展开，不再 [object Object]", () => {
    // 形状来自 admin_console.py:289-296：before/after = {plan_code, subscription:{id,total,used,reserved,remaining}}
    const before = { plan_code: "free", subscription: { id: "sub-acme", total: 10000000, used: 5000, reserved: 1000, remaining: 9994000 } };
    const after = { plan_code: "huading", subscription: { id: "sub-acme", total: 10000000, used: 5000, reserved: 1000, remaining: 9994000 } };
    const leaves = flattenAuditDiff(before, after);
    expect(leaves).toEqual([
      { key: "plan_code", before: "free", after: "huading" },
      { key: "subscription.id", before: "sub-acme", after: "sub-acme" },
      { key: "subscription.total", before: "10,000,000", after: "10,000,000" },
      { key: "subscription.used", before: "5,000", after: "5,000" },
      { key: "subscription.reserved", before: "1,000", after: "1,000" },
      { key: "subscription.remaining", before: "9,994,000", after: "9,994,000" }
    ]);
    // 反 [object Object] 附加负断言（主断言已是上面的确定值 toEqual）。
    expect(JSON.stringify(leaves)).not.toContain("[object Object]");
  });

  it("空数组雷 + 布尔雷（voice_slot_assign，:371-375）→ 前 — / 布尔中文 / 单侧 after 只显不画箭头", () => {
    // 形状来自 admin_console.py:371-375
    const before = { speaker_ids: [] as string[] };
    const after = { speaker_ids: ["S_acme_001"], speaker_id: "S_acme_001", changed: true };
    expect(flattenAuditDiff(before, after)).toEqual([
      { key: "speaker_ids", before: "—", after: "S_acme_001" }, // 空数组 → —
      { key: "speaker_id", after: "S_acme_001" }, // after 独有 → 无 before 字段
      { key: "changed", after: "是" } // 布尔雷 → 是
    ]);
  });

  it("null 雷（task_retry video 主路，:450-476）→ error_code/error_message null → —", () => {
    // 形状来自 admin_console.py:450-476（error_code/error_message 可能为 null）
    const before = { status: "failed", progress: 40, error_code: null, error_message: null };
    const after = { status: "queued", progress: 0 };
    expect(flattenAuditDiff(before, after)).toEqual([
      { key: "status", before: "failed", after: "queued" },
      { key: "progress", before: "40", after: "0" },
      { key: "error_code", before: "—" }, // before 独有（after 无此键）+ null → —
      { key: "error_message", before: "—" }
    ]);
  });

  it("credits_adjust 扁平（:252-253，本就正常）→ 数字全部千分位、键不带 subscription. 前缀", () => {
    // 形状来自 admin_console.py:252-253（扁平 {id,total,used,reserved,remaining}）
    const before = { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 };
    const after = { id: "sub-acme", total: 25000, used: 5000, reserved: 1000, remaining: 19000 };
    expect(flattenAuditDiff(before, after)).toEqual([
      { key: "id", before: "sub-acme", after: "sub-acme" },
      { key: "total", before: "20,000", after: "25,000" },
      { key: "used", before: "5,000", after: "5,000" },
      { key: "reserved", before: "1,000", after: "1,000" },
      { key: "remaining", before: "14,000", after: "19,000" }
    ]);
  });

  it("键序：先 before 键序、再 after 独有键（沿用原 union 语义）；null 快照 → 空列表", () => {
    const leaves = flattenAuditDiff({ b1: "x", shared: "1" }, { shared: "2", a1: "y" });
    expect(leaves.map((l) => l.key)).toEqual(["b1", "shared", "a1"]);
    expect(flattenAuditDiff(null, null)).toEqual([]);
  });
});
