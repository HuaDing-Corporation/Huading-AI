"use client";

import { useState } from "react";
import { Layers, Table } from "lucide-react";

import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger, tabTriggerClass } from "@/components/ui/tabs";
import { EcomTableForm } from "@/components/batch/ecom-table-form";
import { PromptSetForm } from "@/components/batch/prompt-set-form";
import { BatchList } from "@/components/batch/batch-list";
import { BatchDetail } from "@/components/batch/batch-detail";
import { copy } from "@/lib/copy";

/**
 * 批量生产中心（BATCH-PROD-UI-0001）——两 Tab 双入口(商品表 / 提示词组) + 批次记录；选批次进详情(轮询进度)。
 * 提交成功后自动跳该批次详情看进度。移动端单列堆叠。
 */
export function BatchCenter() {
  const [detailId, setDetailId] = useState<string | null>(null);

  if (detailId) {
    return <BatchDetail batchId={detailId} onBack={() => setDetailId(null)} />;
  }

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]">
      <Card animateIn>
        <CardTitle>{copy.batch.pageTitle}</CardTitle>
        <CardSubtitle className="mb-[16px] mt-1">{copy.batch.pageSubtitle}</CardSubtitle>
        <Tabs defaultValue="ecom_table">
          <TabsList aria-label={copy.batch.pageTitle} className="mb-4 flex flex-wrap gap-1.5 rounded-pill border border-line-gold bg-glass-soft p-1">
            <TabsTrigger value="ecom_table" className={tabTriggerClass}>
              <Table size={14} strokeWidth={1.8} /> {copy.batch.tabEcom}
            </TabsTrigger>
            <TabsTrigger value="prompt_set" className={tabTriggerClass}>
              <Layers size={14} strokeWidth={1.8} /> {copy.batch.tabPrompt}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="ecom_table" className="outline-none">
            <EcomTableForm onCreated={setDetailId} />
          </TabsContent>
          <TabsContent value="prompt_set" className="outline-none">
            <PromptSetForm onCreated={setDetailId} />
          </TabsContent>
        </Tabs>
      </Card>

      <BatchList onOpen={setDetailId} />
    </div>
  );
}
