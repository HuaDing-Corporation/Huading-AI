"use client";

import { Clapperboard, FileText, Images, Store, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger, tabTriggerClass } from "@/components/ui/tabs";
import { CopyDraftList } from "@/components/tasks/copy-draft-list";
import { TaskCard } from "@/components/tasks/task-card";
import { useVideoHistory } from "@/lib/api/hooks";
import { fromVideoRead } from "@/lib/sse/progress-mapping";
import { copy } from "@/lib/copy";

/** One mode's history: paginated GET /videos?mode= via useVideoHistory; reuses
 *  TaskCard (list items mapped through fromVideoRead). Loading/error/empty states.
 *  Exported for direct unit testing per mode (Radix tab activation is unreliable
 *  to drive in jsdom). */
export function HistoryList({ mode }: { mode: string }) {
  const router = useRouter();
  const query = useVideoHistory(mode);
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  if (query.isLoading) {
    return <p className="py-10 text-center text-[13px] text-ink-soft">{copy.history.loading}</p>;
  }
  if (query.isError) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center">
        <p className="text-[13px] text-error-fg">{copy.history.error}</p>
        <Button variant="soft" size="sm" onClick={() => void query.refetch()}>
          {copy.history.retry}
        </Button>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-12 text-center">
        <Clapperboard size={26} strokeWidth={1.6} className="text-ink-faint" />
        <p className="text-[13px] text-ink-soft">{copy.history.empty}</p>
      </div>
    );
  }
  return (
    <div>
      {items.map((item) => (
        <TaskCard
          key={item.id}
          // The tab's mode is authoritative for this list → photo items render <img>.
          task={{ ...fromVideoRead(item), mode }}
          onOpen={(id) => router.push(`/videos/${id}`)}
          onRetry={() => undefined}
          onUrlError={() => void query.refetch()}
        />
      ))}
      {query.hasNextPage ? (
        <div className="mt-3 flex justify-center">
          <Button
            variant="soft"
            size="sm"
            onClick={() => void query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
          >
            {copy.history.loadMore}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** 历史生成 — 4 tabs: 数字人 / 电商 / 照片 + 文案. */
export function GenerationHistory() {
  return (
    <Card animateIn>
      <CardTitle className="mb-3.5">{copy.history.title}</CardTitle>
      <Tabs defaultValue="avatar_talk">
        <TabsList
          aria-label={copy.history.title}
          className="mb-3 flex flex-wrap gap-1.5 rounded-pill border border-line-gold bg-glass-soft p-1"
        >
          <TabsTrigger value="avatar_talk" className={tabTriggerClass}>
            <UserRound size={14} strokeWidth={1.8} /> {copy.history.tabAvatar}
          </TabsTrigger>
          <TabsTrigger value="seedance_i2v" className={tabTriggerClass}>
            <Store size={14} strokeWidth={1.8} /> {copy.history.tabEcom}
          </TabsTrigger>
          <TabsTrigger value="photo" className={tabTriggerClass}>
            <Images size={14} strokeWidth={1.8} /> {copy.history.tabPhoto}
          </TabsTrigger>
          <TabsTrigger value="copywriting" className={tabTriggerClass}>
            <FileText size={14} strokeWidth={1.8} /> {copy.history.tabCopy}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="avatar_talk" className="outline-none">
          <HistoryList mode="avatar_talk" />
        </TabsContent>
        <TabsContent value="seedance_i2v" className="outline-none">
          <HistoryList mode="seedance_i2v" />
        </TabsContent>
        <TabsContent value="photo" className="outline-none">
          <HistoryList mode="photo" />
        </TabsContent>
        <TabsContent value="copywriting" className="outline-none">
          <CopyDraftList />
        </TabsContent>
      </Tabs>
    </Card>
  );
}
