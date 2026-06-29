"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";

import { errorText } from "@/lib/api/error-text";
import { useCreatePublishDrafts, usePublishPlatforms } from "@/lib/api/hooks";
import type { CreateDraftsResponse, PublishPlatformId } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardSubtitle, CardTitle } from "@/components/ui/card";
import { SelectableOption } from "@/components/ui/selectable-option";
import { PublishDraftCard } from "@/components/publish/publish-draft-card";
import { PublishRecords } from "@/components/publish/publish-records";
import { copy } from "@/lib/copy";

const labelClass = "mb-2 block text-[12.5px] tracking-[.5px] text-ink-soft";

/**
 * 发布中心主体（PUBLISH-UI-0001）。从 query 取产物(source_kind/source_task_id)→ 多选平台 →
 * POST /publish/drafts 生成各平台草稿卡。下方为发布记录列表。唯一 platforms/drafts hooks 调用方。
 */
export function PublishCenter() {
  const params = useSearchParams();
  const sourceKind = params.get("source_kind") ?? "video";
  const sourceTaskId = params.get("source_task_id");

  const platforms = usePublishPlatforms();
  const createDrafts = useCreatePublishDrafts();

  const [selected, setSelected] = useState<Set<PublishPlatformId>>(new Set());
  const [created, setCreated] = useState<CreateDraftsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const platformList = platforms.data ?? [];
  const nameOf = (id: PublishPlatformId) => platformList.find((p) => p.id === id)?.name ?? id;

  const toggle = (id: PublishPlatformId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onGenerate = async () => {
    setError(null);
    if (!sourceTaskId || selected.size === 0) {
      setError(copy.publish.selectAtLeastOne);
      return;
    }
    try {
      const res = await createDrafts.mutateAsync({
        source_kind: sourceKind === "image" ? "image" : "video",
        source_task_id: sourceTaskId,
        platforms: [...selected]
      });
      setCreated(res);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const generateDisabled = createDrafts.isPending || !sourceTaskId || selected.size === 0;

  return (
    <div className="flex flex-col gap-5">
      <Card animateIn>
        <CardTitle>{copy.publish.pageTitle}</CardTitle>
        <CardSubtitle className="mb-[18px] mt-1">{copy.publish.pageSubtitle}</CardSubtitle>

        {!sourceTaskId ? (
          <p className="text-[13px] text-ink-soft">{copy.publish.noSourceHint}</p>
        ) : (
          <>
            <fieldset className="mb-[15px] m-0 min-w-0 border-0 p-0">
              <legend className={labelClass}>{copy.publish.platformLabel}</legend>
              {platforms.isLoading ? (
                <p className="text-[12.5px] text-ink-soft" aria-live="polite">
                  {copy.publish.recordsLoading}
                </p>
              ) : platforms.isError ? (
                <p role="alert" className="text-[12.5px] text-error-fg">
                  {copy.publish.recordsError}
                </p>
              ) : (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {platformList.map((p) => (
                    <SelectableOption key={p.id} selected={selected.has(p.id)} onSelect={() => toggle(p.id)} className="justify-center">
                      {p.name}
                    </SelectableOption>
                  ))}
                </div>
              )}
            </fieldset>

            {error && (
              <p role="alert" className="mb-3 rounded-field bg-error-bg px-3 py-2 text-[13px] text-error-fg">
                {error}
              </p>
            )}

            <Button variant="primary" size="lg" className="w-full" onClick={() => void onGenerate()} disabled={generateDisabled}>
              {createDrafts.isPending ? copy.publish.generating : copy.publish.generateDrafts}
            </Button>
          </>
        )}
      </Card>

      {created && created.items.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {created.items.map((it) => (
            <PublishDraftCard key={it.platform_id} recordId={created.id} item={it} platformName={nameOf(it.platform_id)} />
          ))}
        </div>
      )}

      <PublishRecords />
    </div>
  );
}
