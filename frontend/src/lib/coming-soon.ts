// 板块「即将上线」占位 gate（UI-COMINGSOON-TENANT-RENAME-0001）。
// 可逆 feature-flag：key 对应 nav.ts 板块 key。true = 导航名加「（即将上线）」+ 点进去显示占位页；
// 未来开放某板块只需把对应 key 置 false（发布中心即恢复真内容/路由；模板中心等待真功能落地后替换占位页）。
// 不删除任何底层组件/路由——发布中心真组件仍在 publish 页内、仅被本 gate 旁路。
export const COMING_SOON: Record<string, boolean> = {
  templates: true, // 模板中心
  brand: true, // 品牌库
  publish: true, // 发布中心（真组件保留，gate 旁路）
  team: true // 团队
};

/** 该板块 key 是否处于「即将上线」占位态。 */
export function isComingSoon(key: string): boolean {
  return COMING_SOON[key] === true;
}
