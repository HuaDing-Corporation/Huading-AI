import {
  BrainCircuit,
  Image as ImageIcon,
  Layers,
  LayoutDashboard,
  LayoutTemplate,
  LineChart,
  Palette,
  Send,
  Users
} from "lucide-react";

import type { NavItem } from "@/components/ui/sidebar-nav";

/**
 * 侧边栏导航项（BATCH-PROD-UI-0001-FIX3）。
 * href 存在 = 已开通路由（Link 真导航 + 按 pathname 高亮）；无 href = 占位项（保留外观，不导航、不高亮，「即将上线」）。
 * UI-COMINGSOON-TENANT-RENAME-0001 / UI-COMINGSOON-COVER-0001：模板中心/品牌库/封面工坊/发布中心/团队 走 coming-soon
 * gate（lib/coming-soon）——均给 href → 可点进统一「即将上线」占位页，导航名由 SidebarNav 按 isComingSoon 追加
 * 「（即将上线）」。工作台/批量生产/图片历史/数据看板 为真板块，不受影响。
 * ADMIN-VIP-GATE-UI-0001：数据看板**始终显示**（去 adminOnly 隐藏）——门禁移到页面：非管理员且非 huading plan
 * 用户点进去看友好页「仅 huading plan 用户可查看」（识别 BE 403 code=ANALYTICS_PLAN_REQUIRED），不隐藏、不报错。
 */
export const navItems: NavItem[] = [
  { key: "workbench", label: "工作台", icon: LayoutDashboard, href: "/" },
  // AIBRAIN-UI-0001：华鼎AI智脑（真上线功能，不进 coming-soon）——索引 1，工作台正下方（只追加，不重排）。
  { key: "aibrain", label: "华鼎AI智脑", icon: BrainCircuit, href: "/aibrain" },
  { key: "batch", label: "批量生产", icon: Layers, href: "/batch" },
  // HISTORY-IMAGE-TAB-UI-0001：左侧「图片历史」下线——已并进工作台「历史生成」的图片 tab（6 分类 + 大图 + 详情合并）。
  { key: "templates", label: "模板中心", icon: LayoutTemplate, href: "/templates" },
  { key: "brand", label: "品牌库", icon: Palette, href: "/brand-library" },
  { key: "covers", label: "封面工坊", icon: ImageIcon, href: "/covers" },
  { key: "publish", label: "发布中心", icon: Send, href: "/publish" },
  { key: "analytics", label: "数据看板", icon: LineChart, href: "/analytics" },
  { key: "team", label: "团队", icon: Users, href: "/team" }
];
