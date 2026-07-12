import type { Session } from "@/lib/auth/store";

/**
 * 品牌音色「升级版 VIP」(doubao 通路) 门禁（ADMIN-VIP-GATE-UI-0001 §二之二）：
 * 放行条件 = 管理员 OR huading plan。前端以 `session.role==="admin"` 或 /me 的 permissions 含
 * `"voice_clone_vip"` 判定（对齐 BE「admin OR plan=huading」；mock 先行，BE 合并后按真实 /me 信号核对）。
 * 免费档 CosyVoice 不受此门禁——任何用户（含 0 余额新注册）都能创建。
 */
export const VIP_VOICE_PERMISSION = "voice_clone_vip";

export function canUseVipVoiceClone(session: Session | null): boolean {
  if (!session) return false;
  if (session.role === "admin") return true;
  return session.user?.permissions?.includes(VIP_VOICE_PERMISSION) ?? false;
}
