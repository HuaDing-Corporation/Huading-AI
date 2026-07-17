// 注册成功 → 工作台欢迎横幅的一次性标记（LANDING-CONTACT-UI-0001）。
//
// 为什么走 localStorage 而不是改注册跳转：硬门 5 要求 #155 的「注册成功 → landToken → 直接进控制台」
// **行为不变** —— 所以提示不能拦在注册页（成功态中转页 = 改了跳转），只能由控制台侧读标记显示。
// 为什么不是 sessionStorage：注册后若立刻关了标签、下次再进控制台，提示仍该在（他还是没额度）——
// 标记**只在用户主动关闭横幅时清除**，不随会话蒸发。这正是「不一闪而过」的存储层含义。
//
// 🔴 标记**存注册者的 email、读时比对当前 session**（review 抓的串号 bug）：localStorage 是设备级的，
// 光存个布尔 → A 注册后没关横幅就退出，同一浏览器上老账号 B 登录会看到「注册成功，欢迎加入华鼎！」——
// 错误的欢迎发给错误的人。绑 email 后：B 登录读到的是 A 的 email → 不显示；A 自己再登录 → 仍显示。
// （比「logout 时清标记」准确：那会把 A 自己重新登录该看到的横幅也误清掉。）
//
// SSR/隐私模式下 localStorage 可能不可用 → 全部 try/catch 吞掉：提示是增强，不值得为它崩注册流程。

const KEY = "hd:welcome-contact";

/** 注册成功时调用（register/page.tsx 成功分支），记下注册者身份。 */
export function markJustRegistered(email: string): void {
  try {
    localStorage.setItem(KEY, email);
  } catch {
    // 存不进去 → 顶多少一次横幅，顶栏常驻入口仍在
  }
}

/** 工作台首屏读：**当前登录者**是不是刚注册的那个人。 */
export function hasWelcomePending(currentEmail: string | undefined): boolean {
  if (!currentEmail) return false;
  try {
    return localStorage.getItem(KEY) === currentEmail;
  } catch {
    return false;
  }
}

/** 用户主动关闭横幅时清除。 */
export function clearWelcomePending(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // 清不掉 → 下次还显示，无害
  }
}
