// 数据看板口径格式化（ANALYTICS-UI-0001）。成本按 cost_cents/100 显示 ¥；不自算毛利/补贴。

/** 分 → 元，¥ 前缀，千分位 + 两位小数。 */
export function yuan(cents: number): string {
  return `¥${(cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** 成功率 0–1 浮点 → 百分比字符串（1 位小数）。 */
export function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/** share_pct 后端已是百分比数值（0–100）→ 直接格式化。 */
export function sharePct(value: number): string {
  return `${value.toFixed(1)}%`;
}

/** 整数千分位（任务量 / 计费笔数 / 租户数等）。 */
export function intFmt(n: number): string {
  return Math.round(n).toLocaleString("zh-CN");
}

/** 积分：可能是浮点，保留最多 1 位小数，去尾零，千分位。 */
export function credits(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return rounded.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
}
