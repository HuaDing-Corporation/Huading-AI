"use client";

import { useState } from "react";
import { Megaphone } from "lucide-react";

import { usePosterBatch, usePosterImage, usePosterTemplates } from "@/lib/api/hooks";
import { Input } from "@/components/ui/input";
import { SelectableOption } from "@/components/ui/selectable-option";
import { EcomImageTool } from "@/components/workbench/ecom-image-tool";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";
const MAX_TITLE = 30;
const MAX_TAGLINE = 40;

/**
 * 电商图 · 营销海报(Phase3) —— 复用 EcomImageTool 外壳，提供「版式预设 + 标题 + 自定义一行」
 * 偏好 + poster 提交。版式预设来自 GET /ecom-images/poster-templates（必选才可生成）；标题/一行
 * 为海报渲染文本(可选)。单张 POST /ecom-images/poster、批量 /poster/batch 返回已创建 photo
 * task(kind=ecom_poster)，由外壳 trackExisting 轮询（产物进 TaskList + 图片历史）。
 */
export function EcomImagePosterForm() {
  const poster = usePosterImage();
  const posterBatch = usePosterBatch();
  const templates = usePosterTemplates();

  const [templateId, setTemplateId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [tagline, setTagline] = useState("");

  const templateList = templates.data ?? [];
  // 后端 title/subtitle 为必填 key(空串允许)：始终发送 trim 后的字符串，不省略 key(否则 422)。
  const titleVal = title.trim();
  const subtitleVal = tagline.trim();

  return (
    <EcomImageTool
      title={copy.workbench.ecomPosterTitle}
      subtitle={copy.workbench.ecomPosterSubtitle}
      idPrefix="ecom-poster"
      icon={<Megaphone size={18} strokeWidth={1.8} />}
      submitting={poster.isPending || posterBatch.isPending}
      extraValid={!!templateId}
      onSubmitSingle={async (assetId) => {
        const res = await poster.mutateAsync({
          source_asset_id: assetId,
          template_id: templateId as string,
          title: titleVal,
          subtitle: subtitleVal
        });
        return [res.task_id];
      }}
      onSubmitBatch={async (assetIds) => {
        const res = await posterBatch.mutateAsync({
          items: assetIds.map((id) => ({ source_asset_id: id, template_id: templateId as string, title: titleVal, subtitle: subtitleVal }))
        });
        return res.tasks.map((t) => t.task_id);
      }}
    >
      {/* 版式预设（必选） */}
      <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
        <legend className={labelClass}>{copy.workbench.ecomTemplateLabel}</legend>
        {templates.isLoading ? (
          <p className="text-[12.5px] text-ink-soft" aria-live="polite">
            {copy.workbench.ecomTemplateLoading}
          </p>
        ) : templates.isError ? (
          <p role="alert" className="text-[12.5px] text-error-fg">
            {copy.workbench.ecomTemplateError}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {templateList.map((t) => (
              <SelectableOption key={t.id} selected={templateId === t.id} onSelect={() => setTemplateId(t.id)} className="justify-center">
                {t.name}
              </SelectableOption>
            ))}
          </div>
        )}
      </fieldset>

      {/* 标题（可选，≤30） */}
      <div className="mb-[15px]">
        <label htmlFor="ecom-poster-title" className={labelClass}>
          {copy.workbench.ecomPosterTitleLabel}
        </label>
        <Input
          id="ecom-poster-title"
          value={title}
          maxLength={MAX_TITLE}
          onChange={(e) => setTitle(e.target.value.slice(0, MAX_TITLE))}
          placeholder={copy.workbench.ecomPosterTitlePlaceholder}
        />
        <p className="mt-1 text-[12px] text-ink-faint">{`${title.length}/${MAX_TITLE}`}</p>
      </div>

      {/* 自定义一行（可选，≤40） */}
      <div className="mb-[15px]">
        <label htmlFor="ecom-poster-tagline" className={labelClass}>
          {copy.workbench.ecomPosterTaglineLabel}
        </label>
        <Input
          id="ecom-poster-tagline"
          value={tagline}
          maxLength={MAX_TAGLINE}
          onChange={(e) => setTagline(e.target.value.slice(0, MAX_TAGLINE))}
          placeholder={copy.workbench.ecomPosterTaglinePlaceholder}
        />
        <p className="mt-1 text-[12px] text-ink-faint">{`${tagline.length}/${MAX_TAGLINE}`}</p>
      </div>
    </EcomImageTool>
  );
}
