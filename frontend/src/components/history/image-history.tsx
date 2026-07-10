"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger, tabTriggerClass } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { HistoryGrid } from "@/components/history/history-grid";
import type { HistoryCategory } from "@/lib/api/history-images";
import { copy } from "@/lib/copy";

// 4 tab 对应归一 category。顺序：图片生成/修改 → 白底图 → 模特图 → 详情图。
const TABS: { key: HistoryCategory; label: string }[] = [
  { key: "image_gen", label: copy.historyImages.tabImageGen },
  { key: "ecom_white", label: copy.historyImages.tabEcomWhite },
  { key: "ecom_model", label: copy.historyImages.tabEcomModel },
  { key: "ecom_detail", label: copy.historyImages.tabEcomDetail }
];

/**
 * 图片历史统一模块（HISTORY-UI-0001）——顶部 4 tab（归一 category）+ 每 tab 一个历史网格。
 * Radix Tabs 默认只挂载激活的 TabsContent → 只查激活分类，切 tab 不串数据。TabsList `flex-wrap` 窄屏换行不溢出。
 */
export function ImageHistory() {
  return (
    <Card>
      <Tabs defaultValue={TABS[0].key}>
        <TabsList
          aria-label={copy.historyImages.pageTitle}
          className="mb-4 flex flex-wrap gap-1.5 rounded-pill border border-line-gold bg-glass-soft p-1"
        >
          {TABS.map((t) => (
            <TabsTrigger key={t.key} value={t.key} className={tabTriggerClass}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {TABS.map((t) => (
          <TabsContent key={t.key} value={t.key} className="outline-none">
            <HistoryGrid category={t.key} />
          </TabsContent>
        ))}
      </Tabs>
    </Card>
  );
}
