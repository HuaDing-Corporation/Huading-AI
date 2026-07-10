import {
  Image as ImageIcon,
  Images,
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
 */
export const navItems: NavItem[] = [
  { key: "workbench", label: "工作台", icon: LayoutDashboard, href: "/" },
  { key: "batch", label: "批量生产", icon: Layers, href: "/batch" },
  { key: "history", label: "图片历史", icon: Images, href: "/history" },
  { key: "templates", label: "模板中心", icon: LayoutTemplate },
  { key: "brand", label: "品牌库", icon: Palette },
  { key: "covers", label: "封面工坊", icon: ImageIcon },
  { key: "publish", label: "发布中心", icon: Send, href: "/publish" },
  { key: "analytics", label: "数据看板", icon: LineChart, href: "/analytics", adminOnly: true },
  { key: "team", label: "团队", icon: Users }
];
