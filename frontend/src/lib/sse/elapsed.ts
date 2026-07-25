/**
 * 已用时长格式化（GEN-HEARTBEAT-UI-0001 · 诚实等待反馈）。
 *
 * 🔴 这里报的是**真实流逝的时间**，不是进度：冻结 §四 明令「不许做假进度条、不许让百分比自己爬」。
 * SPIKE 时代的教训是「低报等待时间属于不诚实的那一侧」→ 故**向下取整到秒、不四舍五入**（宁可
 * 显示 3 分 19 秒也不把 3 分 19.6 秒说成 3 分 20 秒），负数（时钟回拨/基准比现在晚）夹到 0。
 *
 * 30 分钟是 HARD_CAP_MS 上限，正常生成到不了小时级；但从列表水合的历史任务可能带着很老的
 * created_at 进来，故仍给出小时位，避免出现「已 1440 分 0 秒」这种一看就不对的串。
 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  if (h > 0) return `${h} 小时 ${m} 分 ${s} 秒`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}
