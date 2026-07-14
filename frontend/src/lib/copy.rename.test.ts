import { describe, expect, it } from "vitest";

import { copy } from "@/lib/copy";

// UI-COMINGSOON-TENANT-RENAME-0001 · 范围二：全站用户可见文案「租户」→「用户」。
// 扫描 copy 全量字符串（含函数式插值），断言无「租户」残留（技术注释/字段名/类型名不在 copy 内，不受影响）。
function collectStrings(node: unknown, out: string[]): void {
  if (typeof node === "string") {
    out.push(node);
    return;
  }
  if (typeof node === "function") {
    // 覆盖 (n) / (a,b,c) / (str) 等常见签名，收集模板串
    const argSets: unknown[][] = [[], [1], [1, 2, 3], ["x"], ["x", "y", "z"], [1, "x"]];
    for (const args of argSets) {
      try {
        const r = (node as (...a: unknown[]) => unknown)(...args);
        if (typeof r === "string") out.push(r);
      } catch {
        /* 参数不匹配 → 跳过 */
      }
    }
    return;
  }
  if (node && typeof node === "object") {
    for (const v of Object.values(node)) collectStrings(v, out);
  }
}

describe("copy 文案·「租户」→「用户」", () => {
  it("copy 全量字符串（含函数式插值）无「租户」残留（管理后台除外）", () => {
    // 豁免 copy.admin（ADMIN-CONSOLE-UI-0001）：管理后台是**平台运营内部工具**（仅 admin_console 可见），
    // 冻结文档通篇用「租户」——它与租户内的「用户」是两个不同概念（租户=组织，owner 邮箱才是用户），
    // 在运营工具里改叫「用户」会造成歧义。改名要求的范围是「全站**用户可见**文案」（客户界面），后台不在内。
    const customerFacing = Object.fromEntries(Object.entries(copy).filter(([key]) => key !== "admin"));
    const strings: string[] = [];
    collectStrings(customerFacing, strings);
    const offenders = strings.filter((s) => s.includes("租户"));
    expect(offenders, `残留「租户」用户可见文案：\n${offenders.join("\n")}`).toEqual([]);
  });

  it("关键 analytics 文案已改「用户」（数据模型仍 tenant，仅展示串）", () => {
    expect(copy.analytics.colTenant).toBe("用户");
    expect(copy.analytics.tenantTitle).toBe("用户排行");
    expect(copy.analytics.ovTenants).toBe("用户数");
    expect(copy.analytics.ovReservedHintAll(3)).toContain("用户");
    expect(copy.analytics.ovReservedHintTop(3)).toContain("用户");
    expect(copy.analytics.pageSubtitle).not.toContain("租户");
  });
});
