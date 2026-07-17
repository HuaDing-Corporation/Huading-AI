// 注册成功 → 工作台欢迎横幅的一次性标记（LANDING-CONTACT-UI-0001）。
//
// 为什么走 localStorage 而不是改注册跳转：硬门 5 要求 #155 的「注册成功 → landToken → 直接进控制台」
// **行为不变** —— 所以提示不能拦在注册页（成功态中转页 = 改了跳转），只能由控制台侧读标记显示。
// 为什么不是 sessionStorage：注册后若立刻关了标签、下次再进控制台，提示仍该在（他还是没额度）——
// 标记**只在用户主动关闭横幅时清除**，不随会话蒸发。这正是「不一闪而过」的存储层含义。
//
// SSR/隐私模式下 localStorage 可能不可用 → 全部 try/catch 吞掉：提示是增强，不值得为它崩注册流程。

const KEY = "hd:welcome-contact";

/** 注册成功时调用（register/page.tsx 成功分支）。 */
export function markJustRegistered(): void {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    // 存不进去 → 顶多少一次横幅，顶栏常驻入口仍在
  }
}

/** 工作台首屏读：要不要显示欢迎横幅。 */
export function hasWelcomePending(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** 用户主动关闭横幅（或已看过二维码）时清除。 */
export function clearWelcomePending(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // 清不掉 → 下次还显示，无害
  }
}
