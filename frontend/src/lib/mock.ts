import {
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
import type { TaskStatus } from "@/components/ui/status-badge";

export const navItems: NavItem[] = [
  { key: "workbench", label: "工作台", icon: LayoutDashboard },
  { key: "batch", label: "批量生产", icon: Layers },
  { key: "templates", label: "模板中心", icon: LayoutTemplate },
  { key: "brand", label: "品牌库", icon: Palette },
  { key: "covers", label: "封面工坊", icon: ImageIcon },
  { key: "publish", label: "发布中心", icon: Send },
  { key: "analytics", label: "数据看板", icon: LineChart },
  { key: "team", label: "团队", icon: Users }
];

export const quota = { plan: "旗舰版", used: 620, total: 1000 };

export interface VideoTask {
  id: string;
  title: string;
  status: TaskStatus;
  progress: number;
  statusLabel: string;
}

export const tasks: VideoTask[] = [
  { id: "1", title: "秋冬羊绒大衣 · 卖点种草", status: "running", progress: 70, statusLabel: "生成中 70%" },
  { id: "2", title: "新品面霜 · 成分解析", status: "done", progress: 100, statusLabel: "已完成" },
  { id: "3", title: "智能扫地机 · 场景演示", status: "done", progress: 100, statusLabel: "已完成" },
  { id: "4", title: "儿童保温杯 · 卖点合集", status: "queued", progress: 6, statusLabel: "排队中" }
];

export const templateOptions = ["人文纪实", "商品融合", "数字人口播"];
export const voiceSizeOptions = ["知性女声", "9:16 竖屏", "品牌音色"];
