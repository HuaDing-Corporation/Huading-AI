"use client";

import * as TabsPrimitive from "@radix-ui/react-tabs";

export const Tabs = TabsPrimitive.Root;
export const TabsList = TabsPrimitive.List;
export const TabsTrigger = TabsPrimitive.Trigger;
export const TabsContent = TabsPrimitive.Content;

/** 共享 tab 触发器样式(pill + 选中态金墨 chip-sel)——历史 tabs 与封面面板复用，避免复制。 */
export const tabTriggerClass =
  "flex items-center gap-1.5 rounded-pill px-3.5 py-1.5 text-[12.5px] text-ink-soft outline-none transition-colors hover:bg-glass-hover focus-visible:shadow-focus-gold data-[state=active]:bg-chip-sel data-[state=active]:font-medium data-[state=active]:text-gold-deep";
