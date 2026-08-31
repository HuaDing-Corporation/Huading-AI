"use client";

import { AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { type AdminVoiceSlotItem } from "@/lib/api/admin-console";
import { useAdminVoiceSlots } from "@/lib/api/hooks";
import { copy } from "@/lib/copy";

export default function AdminVoiceSlotsPage() {
  const slots = useAdminVoiceSlots();
  const items = slots.data?.items ?? [];
  const columns: AdminColumn<AdminVoiceSlotItem>[] = [
    { key: "speaker", label: copy.admin.colSpeakerId, render: (item) => <span className="break-all tabular-nums text-ink">{item.speaker_id}</span> },
    { key: "scope", label: "来源", render: (item) => <span className="text-ink-soft">{item.scope === "platform" ? "平台登记" : "租户登记"}</span> },
    { key: "tenant", label: copy.admin.colTenant, render: (item) => <span className="text-ink">{item.tenant_slug ?? "—"}</span> },
    { key: "voice", label: copy.admin.colVoiceName, render: (item) => <span className="text-ink-soft">{item.brand_voice_name ?? "—"}</span> },
    { key: "conflict", label: "冲突状态", render: (item) => <span className={item.occupied ? "text-queue-fg" : "text-success-fg"}>{item.occupied ? "已登记/占用" : "未占用"}</span> }
  ];

  return (
    <>
      <header className="px-1">
        <h1 className="text-[22px] font-semibold tracking-[.5px] text-ink">{copy.admin.voiceSlotsTitle}</h1>
        <p className="mt-1 text-[12.5px] leading-5 text-ink-soft">旧槽位分配入口已退役。本页仅用于核对平台/租户登记与冲突，不提供写操作。</p>
      </header>
      <AdminTable
        columns={columns}
        rows={items}
        rowKey={(item) => `${item.scope}:${item.tenant_id ?? "platform"}:${item.speaker_id}`}
        loading={slots.isLoading}
        error={slots.isError}
        onRetry={() => void slots.refetch()}
        minWidth={680}
      />
    </>
  );
}
