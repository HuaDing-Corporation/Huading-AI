// 注册成功 → 工作台欢迎横幅的一次性标记（LANDING-CONTACT-UI-0001 · FIX1）。
//
// 为什么走 localStorage 而不是改注册跳转：硬门要求 #155 的「注册成功 → landToken → 直接进控制台」
// **行为不变** —— 提示不能拦在注册页（成功态中转页 = 改了跳转），只能由控制台侧读标记显示。
// 为什么不是 sessionStorage：注册后若立刻关了标签、下次再进控制台，提示仍该在（他还是没额度）——
// 标记**只在用户主动关闭横幅时清除**，不随会话蒸发。这正是「不一闪而过」的存储层含义。
//
// 🔴 FIX1：身份从 **email 改成 `tenantId + userId`**，且**每身份一把 key**。review1 用 email 绑定，
// Codex B 核出四条坏路径，逐条为什么这样改能修掉：
//  ① **同 email 不同 tenant**：BE 唯一约束是 `(tenant_id, email)`，同一 email 可注册多个租户 →
//     email 不是身份。tenantId+userId 才是。
//  ② **大写 email 永不匹配**：BE 把 email 转小写存，注册页存的是**未转小写**的输入 → session 返回
//     小写、标记是大写 → 恒不等。tenantId/userId 是 BE 下发的 ID，读写同源自同一个 token，无大小写问题。
//  ③ **session 换人横幅不消失**：见 welcome-contact-banner —— effect 现在把可见性**等于完整匹配结果**
//     （匹配则显、不匹配则隐），不再只在匹配时置 true。本模块只负责「当前身份是否有标记」。
//  ④ **多标签页互删**：单一设备级 key 会被后写覆盖、清除会误删别人的。**每身份一把 key**（keyFor）
//     → 两身份的标记能共存、清除只动当前身份那把。是否该支持多身份共存的判断见 PR body / 回执：
//     结论是「支持」——分 key 成本是几字节的罕见残留（只在注册时写、关闭时清），不值得为它建 GC。
//
// SSR/隐私模式下 localStorage 可能不可用 → 全部 try/catch 吞掉：提示是增强，不值得为它崩注册流程。

/** 每身份一把 key —— tenantId+userId 唯一确定「哪个账号在哪个租户」。 */
function keyFor(tenantId: string, userId: string): string {
  return `hd:welcome-contact:${tenantId}:${userId}`;
}

/** 注册成功时调用（register/page.tsx 成功分支），记下注册者身份。 */
export function markJustRegistered(tenantId: string, userId: string): void {
  try {
    localStorage.setItem(keyFor(tenantId, userId), "1");
  } catch {
    // 存不进去 → 顶多少一次横幅，顶栏常驻入口仍在
  }
}

/** 工作台读：**当前登录身份**是不是刚注册的那个（tenantId 或 userId 缺一即否）。 */
export function hasWelcomePending(tenantId: string | undefined, userId: string | undefined): boolean {
  if (!tenantId || !userId) return false;
  try {
    return localStorage.getItem(keyFor(tenantId, userId)) === "1";
  } catch {
    return false;
  }
}

/** 用户主动关闭横幅时清除 —— **只清当前身份**那把 key（不碰同设备其它身份的标记，修 ④）。 */
export function clearWelcomePending(tenantId: string | undefined, userId: string | undefined): void {
  if (!tenantId || !userId) return;
  try {
    localStorage.removeItem(keyFor(tenantId, userId));
  } catch {
    // 清不掉 → 下次还显示，无害
  }
}
