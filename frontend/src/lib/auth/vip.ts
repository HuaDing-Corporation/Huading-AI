import type { Session } from "@/lib/auth/store";

/**
 * 前端 entitlement 判据（PROD-P0-ANALYTICS-TENANT-LEAK-UI-0001）——**门禁一律走 /auth/me.permissions，绝不走 role**。
 *
 * 🔴 P0 根因：自助注册的租户 owner 在后端也是 role=ADMIN（租户管理员 ≠ 平台超管，auth.py），
 * 旧 `session.role==="admin"` 分支会让 free 新注册用户绕过门禁（看到别租户数据、升级版卡不置灰）。
 * BE 已把三个 entitlement 改为按「平台租户 OR huading plan」实时派生进 permissions，前端只认 permissions。
 */
export const VIP_VOICE_PERMISSION = "voice_clone_vip";
export const ANALYTICS_VIEW_PERMISSION = "analytics_view";
export const ANALYTICS_PLATFORM_PERMISSION = "analytics_platform";

function hasPermission(session: Session | null, permission: string): boolean {
  return session?.user?.permissions?.includes(permission) ?? false;
}

/**
 * 品牌音色「升级版 VIP」(doubao 通路) 门禁——唯一信号 = permissions 含 `voice_clone_vip`
 * （BE 派生「平台租户 OR huading plan」）。免费档 CosyVoice 不受此门禁（任何用户可建）。
 */
export function canUseVipVoiceClone(session: Session | null): boolean {
  return hasPermission(session, VIP_VOICE_PERMISSION);
}

/** 能否进入数据看板：permissions 含 `analytics_view`（平台租户 OR huading plan）。无 → BE 403 → VIP 友好页。 */
export function canViewAnalytics(session: Session | null): boolean {
  return hasPermission(session, ANALYTICS_VIEW_PERMISSION);
}

/**
 * 能否看**全站视图**：permissions 含 `analytics_platform`（仅平台租户）。
 * 无此权限的 VIP 客户只看自己的数据——前端据此隐藏「用户排行」、副标题改「我的用量」。
 */
export function canViewPlatformAnalytics(session: Session | null): boolean {
  return hasPermission(session, ANALYTICS_PLATFORM_PERMISSION);
}

/**
 * 管理员后台 `/admin` 门禁（ADMIN-CONSOLE-UI-0001）：permissions 含 `admin_console`（BE 与
 * `analytics_platform` 同源派生 = 仅平台租户）。无 → 友好页「仅平台管理员可访问」，BE 端点也会真 403
 * PLATFORM_ADMIN_REQUIRED。同样**绝不看 role**（自助注册 owner 全是 role=admin）。
 */
export const ADMIN_CONSOLE_PERMISSION = "admin_console";

export function canUseAdminConsole(session: Session | null): boolean {
  return hasPermission(session, ADMIN_CONSOLE_PERMISSION);
}
