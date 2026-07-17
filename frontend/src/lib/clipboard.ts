// 唯一的剪贴板写入（CLIPBOARD-TRUTH-0001）。
//
// 🔴 这里收口的是**一个 bug 的三份拷贝**：本仓曾有三处手写 clipboard 逻辑，其中两处写成
//   `await navigator.clipboard?.writeText(x); setCopied(true)`
// —— `?.` 在 `navigator.clipboard` 缺失（非安全上下文 http:// / 老浏览器 / WebView）时**短路成 undefined**，
// `await undefined` **不抛异常** → 紧跟的 `setCopied(true)` 照跑 → **界面说「已复制」，剪贴板里什么都没有**。
// copyable-block 那份是对的（守卫在前、真实调用无 `?.`），但「对的手写拷贝」仍是拷贝：留着它当模板，
// 下一个人照它抄仍可能抄错。今天在 useMediaUrlRefresh 上刚治过同款「三份拷贝已漂成两个样」——
// 收敛的意义不是少写几行，是**让这段逻辑只有一处、无从再漂**。三处消费者（copywriting-form /
// publish-draft-card / copyable-block）现在都调本函数，成功态一律由**返回的布尔**驱动，不再各自乐观置态。
//
// 返回值语义：**true 仅当文本真的写进了剪贴板**。以下一律 false（调用方据此**不**显示「已复制」）：
//  - 空文本（没内容可复制）
//  - Clipboard API 不存在（非安全上下文 / 老浏览器）—— 关键：判存在性用 `?.`，真正的写入调用**不带** `?.`
//  - writeText 抛错（用户拒权 / 浏览器拦截）
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text || !navigator.clipboard?.writeText) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false; // 复制失败 → 调用方静默降级或提示手动复制，但绝不谎报成功
  }
}
