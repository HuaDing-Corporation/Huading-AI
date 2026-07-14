"use client";

import { useState } from "react";

import { AdminTable, type AdminColumn } from "@/components/admin/admin-table";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SPEAKER_ID_PATTERN, type AdminVoiceSlotItem } from "@/lib/api/admin-console";
import { useAdminTenants, useAdminVoiceSlots, useAssignVoiceSlot } from "@/lib/api/hooks";
import { errorText } from "@/lib/api/error-text";
import { copy } from "@/lib/copy";

// 音色槽位（ADMIN-CONSOLE-UI-0001 · FIX1 对齐真契约）：BE 返回**合一列表**（AdminVoiceSlotItem，scope 区分
// 平台池/租户专属）+ remaining；分配走 POST /tenants/{id}/voice-slots（幂等 changed:false 不报错）。
export default function AdminVoiceSlotsPage() {
  const slots = useAdminVoiceSlots();
  const tenants = useAdminTenants({ sort: "created_at", order: "desc", page: 1, page_size: 100 });
  const assign = useAssignVoiceSlot();
  const [tenantId, setTenantId] = useState("");
  const [speakerId, setSpeakerId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const items = slots.data?.items ?? [];
  const platformPool = items.filter((s) => s.scope === "platform");
  const tenantSlots = items.filter((s) => s.scope === "tenant");

  const poolColumns: AdminColumn<AdminVoiceSlotItem>[] = [
    { key: "speaker", label: copy.admin.colSpeakerId, render: (s) => <span className="tabular-nums text-ink">{s.speaker_id}</span> },
    {
      key: "occupied",
      label: copy.admin.colOccupiedBy,
      render: (s) =>
        s.occupied ? <span className="text-ink">{s.tenant_slug ?? "—"}</span> : <span className="text-success-fg">{copy.admin.slotFree}</span>
    },
    { key: "voice", label: copy.admin.colVoiceName, render: (s) => <span className="text-ink-soft">{s.brand_voice_name ?? "—"}</span> }
  ];
  const tenantColumns: AdminColumn<AdminVoiceSlotItem>[] = [
    { key: "tenant", label: copy.admin.colTenant, render: (s) => <span className="text-ink">{s.tenant_slug ?? "—"}</span> },
    { key: "speaker", label: copy.admin.colSpeakerId, render: (s) => <span className="tabular-nums text-ink">{s.speaker_id}</span> },
    { key: "voice", label: copy.admin.colVoiceName, render: (s) => <span className="text-ink-soft">{s.brand_voice_name ?? "—"}</span> }
  ];

  const onAssign = () => {
    setDone(false);
    if (!tenantId) return setFormError(copy.admin.assignTenantLabel + "必选");
    if (!SPEAKER_ID_PATTERN.test(speakerId)) return setFormError(copy.admin.assignSpeakerInvalid);
    setFormError(null);
    if (assign.isPending) return;
    assign.mutate({ tenantId, speaker_id: speakerId }, { onSuccess: () => setDone(true) });
  };

  return (
    <>
      <header className="px-1">
        <h1 className="text-[22px] font-semibold tracking-[0.5px] text-ink">{copy.admin.voiceSlotsTitle}</h1>
      </header>

      <section className="flex flex-col gap-2">
        <h2 className="text-[15px] font-semibold text-ink">
          {copy.admin.poolTitle}
          <span className="ml-2 text-[12px] font-normal text-ink-soft">{copy.admin.poolRemaining(slots.data?.remaining ?? 0)}</span>
        </h2>
        <AdminTable
          columns={poolColumns}
          rows={platformPool}
          rowKey={(s) => s.speaker_id}
          loading={slots.isLoading}
          error={slots.isError}
          onRetry={() => void slots.refetch()}
          minWidth={520}
        />
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-[15px] font-semibold text-ink">{copy.admin.tenantSlotsTitle}</h2>
        <AdminTable
          columns={tenantColumns}
          rows={tenantSlots}
          rowKey={(s) => `${s.tenant_id}:${s.speaker_id}`}
          loading={slots.isLoading}
          error={slots.isError}
          onRetry={() => void slots.refetch()}
          minWidth={520}
        />
      </section>

      <section className="flex max-w-md flex-col gap-2 rounded-field border border-line-gold bg-glass-soft p-4">
        <h2 className="text-[15px] font-semibold text-ink">{copy.admin.assignTitle}</h2>
        <label className="text-[12px] text-ink-soft">{copy.admin.assignTenantLabel}</label>
        <Select value={tenantId || undefined} onValueChange={setTenantId}>
          <SelectTrigger className="w-full" aria-label={copy.admin.assignTenantLabel}>
            <SelectValue placeholder={copy.admin.assignTenantLabel} />
          </SelectTrigger>
          <SelectContent>
            {(tenants.data?.items ?? []).map((t) => (
              <SelectItem key={t.tenant_id} value={t.tenant_id}>
                {t.name}（{t.slug}）
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <label className="text-[12px] text-ink-soft" htmlFor="assign-speaker">
          {copy.admin.assignSpeakerLabel}
        </label>
        <Input
          id="assign-speaker"
          value={speakerId}
          onChange={(e) => setSpeakerId(e.target.value.trim())}
          placeholder={copy.admin.assignSpeakerPlaceholder}
        />
        {formError && (
          <p role="alert" className="text-[12px] text-error-fg">
            {formError}
          </p>
        )}
        {assign.isError && (
          <p role="alert" className="text-[12px] text-error-fg">
            {errorText(assign.error)}
          </p>
        )}
        {done && (
          <p role="status" className="text-[12px] text-success-fg">
            {copy.admin.assignDone}
          </p>
        )}
        <button
          type="button"
          onClick={onAssign}
          disabled={assign.isPending}
          className="self-start rounded-field border border-line-gold bg-glass-fill px-4 py-1.5 text-[12.5px] text-gold-deep hover:bg-glass-hover disabled:opacity-50"
        >
          {assign.isPending ? copy.common.processing : copy.admin.assignSubmit}
        </button>
      </section>
    </>
  );
}
