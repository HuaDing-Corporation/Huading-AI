import { describe, expect, it } from "vitest";

import { flattenAuditDiff, formatAuditValue, truncateAuditValue } from "./format";

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
      { key: "plan_code", before: "free", after: "huading", changed: true },
      { key: "subscription.id", before: "sub-acme", after: "sub-acme", changed: false },
      { key: "subscription.total", before: "10,000,000", after: "10,000,000", changed: false },
      { key: "subscription.used", before: "5,000", after: "5,000", changed: false },
      { key: "subscription.reserved", before: "1,000", after: "1,000", changed: false },
      { key: "subscription.remaining", before: "9,994,000", after: "9,994,000", changed: false }
    ]);
    // 反 [object Object] 附加负断言（主断言已是上面的确定值 toEqual）。
    expect(JSON.stringify(leaves)).not.toContain("[object Object]");
  });

  it("空数组雷 + 布尔雷（voice_slot_assign，:371-375）→ 前 — / 布尔中文 / 单侧 after 只显不画箭头", () => {
    // 形状来自 admin_console.py:371-375
    const before = { speaker_ids: [] as string[] };
    const after = { speaker_ids: ["S_acme_001"], speaker_id: "S_acme_001", changed: true };
    expect(flattenAuditDiff(before, after)).toEqual([
      { key: "speaker_ids", before: "—", after: "S_acme_001", changed: true }, // 空数组 → —，且变化
      { key: "speaker_id", after: "S_acme_001", changed: true }, // after 独有（单边键）→ 恒变化、默认可见
      { key: "changed", after: "是", changed: true } // 布尔雷 → 是；单边键
    ]);
  });

  it("null 雷（task_retry video 主路，:450-476）→ error_code/error_message null → —", () => {
    // 形状来自 admin_console.py:450-476（error_code/error_message 可能为 null）
    const before = { status: "failed", progress: 40, error_code: null, error_message: null };
    const after = { status: "queued", progress: 0 };
    expect(flattenAuditDiff(before, after)).toEqual([
      { key: "status", before: "failed", after: "queued", changed: true },
      { key: "progress", before: "40", after: "0", changed: true },
      { key: "error_code", before: "—", changed: true }, // before 独有（after 无此键）+ null → —；单边键
      { key: "error_message", before: "—", changed: true }
    ]);
  });

  it("credits_adjust 扁平（:252-253，本就正常）→ 数字全部千分位、键不带 subscription. 前缀", () => {
    // 形状来自 admin_console.py:252-253（扁平 {id,total,used,reserved,remaining}）
    const before = { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 };
    const after = { id: "sub-acme", total: 25000, used: 5000, reserved: 1000, remaining: 19000 };
    expect(flattenAuditDiff(before, after)).toEqual([
      { key: "id", before: "sub-acme", after: "sub-acme", changed: false },
      { key: "total", before: "20,000", after: "25,000", changed: true },
      { key: "used", before: "5,000", after: "5,000", changed: false },
      { key: "reserved", before: "1,000", after: "1,000", changed: false },
      { key: "remaining", before: "14,000", after: "19,000", changed: true }
    ]);
  });

  it("键序：先 before 键序、再 after 独有键（沿用原 union 语义）；null 快照 → 空列表", () => {
    const leaves = flattenAuditDiff({ b1: "x", shared: "1" }, { shared: "2", a1: "y" });
    expect(leaves.map((l) => l.key)).toEqual(["b1", "shared", "a1"]);
    expect(flattenAuditDiff(null, null)).toEqual([]);
  });
});

// ADMIN-AUDIT-DIFF-NOISE-0001 §四：变化分组（未变键折叠、信息只折叠不删）+ 长值中截。期望值手写常量。
describe("变化分组 changed 标志（§一 真实案例 + 单边键）", () => {
  it("§一 生产实拍套餐变更：只有 plan_code 变、subscription.* 5 项全未变 → 分组精确", () => {
    // 形状/值照抄 §一（admin_console.py:289-296）：plan free→huading，余额分文未动。
    const before = { plan_code: "free", subscription: { id: "8a4edef5-4075-4dce-a031-8a6ece6618fc", used: 110, total: 10000000, reserved: 0, remaining: 9999890 } };
    const after = { plan_code: "huading", subscription: { id: "8a4edef5-4075-4dce-a031-8a6ece6618fc", used: 110, total: 10000000, reserved: 0, remaining: 9999890 } };
    const leaves = flattenAuditDiff(before, after);
    // 默认展示（变化）恰 plan_code 一项；折叠（未变）恰 subscription.* 五项——保持键序。
    expect(leaves.filter((l) => l.changed).map((l) => l.key)).toEqual(["plan_code"]);
    expect(leaves.filter((l) => !l.changed).map((l) => l.key)).toEqual([
      "subscription.id",
      "subscription.used",
      "subscription.total",
      "subscription.reserved",
      "subscription.remaining"
    ]);
  });

  it("🔴 单边键（voice_slot 首次分配：只有 after）必判「变化」、默认可见——最易写错的一条", () => {
    const leaves = flattenAuditDiff({ speaker_ids: [] }, { speaker_ids: ["S_x"], speaker_id: "S_x", changed: true });
    expect(leaves.every((l) => l.changed)).toBe(true); // 三键全变化（无未变键）
    expect(leaves.filter((l) => !l.changed)).toEqual([]);
  });

  it("全部键都变（credits_adjust total/remaining 变）→ 有未变键（id/used/reserved）仍在，分组各就位", () => {
    const leaves = flattenAuditDiff(
      { id: "sub-acme", total: 20000, used: 5000, reserved: 1000, remaining: 14000 },
      { id: "sub-acme", total: 25000, used: 5000, reserved: 1000, remaining: 19000 }
    );
    expect(leaves.filter((l) => l.changed).map((l) => l.key)).toEqual(["total", "remaining"]);
    expect(leaves.filter((l) => !l.changed).map((l) => l.key)).toEqual(["id", "used", "reserved"]);
  });
});

// FIX1（PR #172 P1）：changed 判定必须基于**原始叶子值深比较**，不能用格式化后字符串——否则碰撞误判「未变」，
// 把「发生变更」这一事实抹掉（违反审计铁律）。变异硬门：判定改回格式化字符串 → 下面 1–4 必全红。
describe("FIX1 · changed 基于原始值深比较（格式化碰撞不再误判「未变」）", () => {
  it("🔴 四个格式化碰撞反例（Codex B 实跑）→ 全判「变化」，信息不丢", () => {
    expect(flattenAuditDiff({ v: "" }, { v: null })[0].changed).toBe(true); // "" → null（都格式化成 —）
    expect(flattenAuditDiff({ v: [] }, { v: null })[0].changed).toBe(true); // [] → null（都 —）
    expect(flattenAuditDiff({ v: {} }, { v: null })[0].changed).toBe(true); // {} → null（都 —）
    expect(flattenAuditDiff({ v: 1 }, { v: "1" })[0].changed).toBe(true); // 1 → "1"（类型变更被吞）
  });

  it("深比较不反向误判：同值数组/标量 → 「未变」；数组内容变 → 「变化」", () => {
    expect(flattenAuditDiff({ v: [] }, { v: [] })[0].changed).toBe(false);
    expect(flattenAuditDiff({ v: [1, 2] }, { v: [1, 2] })[0].changed).toBe(false);
    expect(flattenAuditDiff({ v: [1, 2] }, { v: [1, 3] })[0].changed).toBe(true);
    expect(flattenAuditDiff({ v: 0 }, { v: 0 })[0].changed).toBe(false);
    expect(flattenAuditDiff({ v: "x" }, { v: "x" })[0].changed).toBe(false);
    expect(flattenAuditDiff({ v: false }, { v: false })[0].changed).toBe(false);
  });

  it("undefined（键不存在）vs null（键存在为空）不混淆：单边键恒变化；null→null 未变", () => {
    expect(flattenAuditDiff({ v: null }, {})[0].changed).toBe(true); // after 无此键（单边）
    expect(flattenAuditDiff({}, { v: null })[0].changed).toBe(true); // before 无此键（单边）
    expect(flattenAuditDiff({ v: null }, { v: null })[0].changed).toBe(false); // 两边都 null → 未变
  });
});

describe("truncateAuditValue（长值中截，全值可得由组件层保证）", () => {
  it("36 位 UUID → 前8…后6 中截；错误码/plan_code/中文消息/短值一律不截", () => {
    const uuid = "8a4edef5-4075-4dce-a031-8a6ece6618fc";
    expect(uuid.length).toBe(36);
    expect(truncateAuditValue(uuid)).toBe("8a4edef5…6618fc");
    // 不截：错误码（非 hex 字母 + 下划线）、plan_code（短）、中文消息（含 CJK）、槽位 id（非 hex 字母）
    expect(truncateAuditValue("TASK_RETRY_ENQUEUE_FAILED")).toBe("TASK_RETRY_ENQUEUE_FAILED");
    expect(truncateAuditValue("huading")).toBe("huading");
    expect(truncateAuditValue("扣减后额度会低于已用与预留，本次操作无法执行，请重新核对。")).toBe("扣减后额度会低于已用与预留，本次操作无法执行，请重新核对。");
    expect(truncateAuditValue("S_acme_001")).toBe("S_acme_001");
  });
});
